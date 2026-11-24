"""Zed language server for the Markdown Extract code action."""

import itertools
import json
import os
from pathlib import Path
from queue import Queue
import re
import subprocess
import sys
from threading import Thread
from urllib.parse import quote, urlsplit
from urllib.request import url2pathname


COMMAND = "markdown-extract.extract"
TITLE = "Extract selection to Markdown file…"
INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_NAME = re.compile(r"CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³]", re.I)


def destination(directory, filename):
    filename = filename.strip()
    if (
        not filename
        or filename.endswith(".")
        or INVALID_NAME.search(filename)
        or RESERVED_NAME.fullmatch(filename.split(".", 1)[0].rstrip())
    ):
        raise ValueError("Enter a filename without a folder path or reserved characters.")
    if not filename.lower().endswith(".md"):
        filename += ".md"
    target = directory / filename
    if os.path.lexists(target):
        raise ValueError("A file or folder with that name already exists.")
    return target


def prompt(directory):
    from tkinter import Tk, messagebox, simpledialog

    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    class FilenameDialog(simpledialog.Dialog):
        def body(self, frame):
            from tkinter import ttk

            ttk.Label(frame, text="Filename for the new Markdown file").pack(
                anchor="w", padx=12, pady=(12, 6)
            )
            self.entry = ttk.Entry(frame, width=44)
            self.entry.pack(padx=12, pady=(0, 8))
            return self.entry

        def validate(self):
            try:
                self.result = destination(directory, self.entry.get()).name
                return True
            except ValueError as error:
                messagebox.showerror("Choose a filename", str(error), parent=self)
                return False

    try:
        return FilenameDialog(root, "Extract Markdown selection").result
    finally:
        root.destroy()


def offset(text, position):
    """Convert an LSP UTF-16 position to a Python string offset."""
    line_start = 0
    for _ in range(position["line"]):
        newline = text.index("\n", line_start)
        line_start = newline + 1
    line_end = text.find("\n", line_start)
    if line_end < 0:
        line_end = len(text)
    column = position["character"]
    units = 0
    for index in range(line_start, line_end):
        if units == column:
            return index
        units += 2 if ord(text[index]) > 0xFFFF else 1
        if units > column:
            break
    if units == column:
        return line_end
    raise ValueError("Zed sent an invalid selection position.")


def span(text, range_):
    return offset(text, range_["start"]), offset(text, range_["end"])


def file_path(uri):
    parsed = urlsplit(uri)
    if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment:
        return None
    path = Path(url2pathname(parsed.path))
    return path if path.is_absolute() and path.is_file() else None


def markdown_link(target, selection):
    label = "".join(
        "\\" + char if char in "\\[]*_~\x60&$" else char for char in target.stem
    )
    # Keep selected boundary line breaks in the source so the link stays on its own line.
    beginning = re.match(r"(?:\r?\n)+", selection)
    ending = re.search(r"(?:\r?\n)+\Z", selection)
    return (
        (beginning[0] if beginning and beginning.end() != len(selection) else "")
        + f"[{label}]({quote(target.name)})"
        + (ending[0] if ending else "")
    )


def read_messages(stream, events):
    try:
        while header := stream.readline():
            headers = {}
            while header not in (b"\r\n", b"\n", b""):
                name, value = header.split(b":", 1)
                headers[name.lower()] = value.strip()
                header = stream.readline()
            size = int(headers[b"content-length"])
            body = stream.read(size)
            if len(body) != size:
                break
            message = json.loads(body)
            events.put(("message", message))
            if message.get("method") == "exit":
                break
    except (OSError, ValueError, KeyError) as error:
        print(f"Markdown Extract protocol error: {error}", file=sys.stderr)
    finally:
        events.put(("eof", None))


class Server:
    def __init__(self):
        self.events = Queue()
        self.documents = {}
        self.serial = itertools.count()
        self.pending = None

    def send(self, **fields):
        body = json.dumps({"jsonrpc": "2.0", **fields}).encode("utf-8")
        sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        sys.stdout.buffer.flush()

    def answer(self, request_id, result=None):
        self.send(id=request_id, result=result)

    def error(self, message):
        self.send(method="window/showMessage", params={"type": 1, "message": message})

    def run(self):
        Thread(target=read_messages, args=(sys.stdin.buffer, self.events), daemon=True).start()
        while True:
            event, data = self.events.get()
            if event == "eof" or (event == "message" and data.get("method") == "exit"):
                return
            if event == "filename":
                self.finish_prompt(*data)
            else:
                self.handle_message(data)

    def handle_message(self, message):
        method = message.get("method")
        params = message.get("params") or {}
        request_id = message.get("id")
        if method == "initialize":
            self.answer(request_id, {
                "capabilities": {
                    "positionEncoding": "utf-16",
                    "textDocumentSync": {"openClose": True, "change": 2},
                    "codeActionProvider": {"codeActionKinds": ["refactor.extract"]},
                    "executeCommandProvider": {"commands": [COMMAND]},
                },
                "serverInfo": {"name": "Markdown Extract", "version": "0.1.0"},
            })
        elif method in ("textDocument/didOpen", "textDocument/didChange"):
            self.update_document(method, params)
        elif method == "textDocument/didClose":
            self.documents.pop(params["textDocument"]["uri"], None)
        elif method == "textDocument/codeAction":
            self.answer(request_id, self.code_actions(params))
        elif method == "workspace/executeCommand":
            self.execute(request_id, params)
        elif method == "shutdown":
            self.answer(request_id)
        elif method is None:
            self.handle_edit_result(message)
        elif "id" in message:
            self.send(id=request_id, error={"code": -32601, "message": "Method not found"})

    def update_document(self, method, params):
        document = params["textDocument"]
        uri = document["uri"]
        if method == "textDocument/didOpen":
            text = document["text"]
        else:
            if uri not in self.documents:
                return
            text = self.documents[uri][0]
            for change in params["contentChanges"]:
                if "range" in change:
                    start, end = span(text, change["range"])
                    text = text[:start] + change["text"] + text[end:]
                else:
                    text = change["text"]
        self.documents[uri] = (text, document["version"], next(self.serial))

    def code_actions(self, params):
        uri = params["textDocument"]["uri"]
        range_ = params["range"]
        only = params.get("context", {}).get("only", [])
        if only and not any(
            kind in ("", "refactor", "refactor.extract") for kind in only
        ):
            return []
        if uri not in self.documents or file_path(uri) is None:
            return []
        text, _, revision = self.documents[uri]
        try:
            start, end = span(text, range_)
        except ValueError:
            return []
        if start >= end:
            return []
        return [{
            "title": TITLE,
            "kind": "refactor.extract",
            "command": {
                "title": TITLE,
                "command": COMMAND,
                "arguments": [uri, range_, revision],
            },
        }]

    def execute(self, request_id, params):
        if params.get("command") != COMMAND:
            self.send(id=request_id, error={"code": -32602, "message": "Unknown command"})
            return
        if self.pending is not None:
            self.error("Finish the current Markdown extraction first.")
            self.answer(request_id)
            return
        uri, range_, revision = params["arguments"]
        source = file_path(uri)
        document = self.documents.get(uri)
        if source is None or document is None or document[2] != revision:
            self.error("The source changed. Select the text again and retry.")
            self.answer(request_id)
            return
        self.pending = {
            "request": request_id, "uri": uri, "range": range_,
            "revision": revision, "source": source,
        }
        Thread(target=self.ask_filename, args=(source.parent,), daemon=True).start()

    def ask_filename(self, directory):
        # Tk runs on the main thread of a child process. The server stays responsive
        # to buffer changes and shutdown while the filename dialog is open.
        try:
            if sys.argv[0] == "-c":
                invocation = ["-c", sys.orig_argv[sys.orig_argv.index("-c") + 1]]
            else:
                invocation = [str(Path(__file__).resolve())]
            child = subprocess.run(
                [sys.executable, "-I", *invocation, "--prompt", str(directory)],
                stdin=subprocess.DEVNULL, capture_output=True, check=True,
                encoding="utf-8", timeout=90,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.events.put(("filename", (json.loads(child.stdout), None)))
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            detail = getattr(error, "stderr", None)
            if detail:
                print(detail, file=sys.stderr)
            message = "Could not open the filename dialog. Check Python's Tk support."
            if isinstance(error, subprocess.TimeoutExpired):
                message = "The filename prompt timed out. Run the action again."
            self.events.put(("filename", (None, message)))

    def finish_prompt(self, filename, error):
        pending = self.pending
        if filename is None:
            if error:
                self.error(error)
            self.finish_request()
            return
        document = self.documents.get(pending["uri"])
        if document is None or document[2] != pending["revision"]:
            self.error("The source changed while the dialog was open. Select the text again and retry.")
            self.finish_request()
            return
        text, version, _ = document
        start, end = span(text, pending["range"])
        selected = text[start:end]
        try:
            target = destination(pending["source"].parent, filename)
            with target.open("x", encoding="utf-8", newline="") as output:
                output.write(selected)
        except (OSError, ValueError) as failure:
            self.error(f"Could not create the Markdown file. {failure}")
            self.finish_request()
            return

        pending["target"] = target
        pending["edit_id"] = f"markdown-extract-{next(self.serial)}"
        self.send(
            id=pending["edit_id"],
            method="workspace/applyEdit",
            params={"label": "Extract Markdown selection", "edit": {"documentChanges": [{
                "textDocument": {"uri": pending["uri"], "version": version},
                "edits": [{"range": pending["range"], "newText": markdown_link(target, selected)}],
            }]}},
        )

    def handle_edit_result(self, message):
        if self.pending is None or message.get("id") != self.pending.get("edit_id"):
            return
        if not (message.get("result") or {}).get("applied"):
            self.error(f'Zed could not insert the link. The extracted text is in "{self.pending["target"].name}".')
        self.finish_request()

    def finish_request(self):
        request_id = self.pending["request"]
        self.pending = None
        self.answer(request_id)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--prompt"]:
        print(json.dumps(prompt(Path(sys.argv[2]))))
    else:
        Server().run()
