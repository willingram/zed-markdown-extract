# Markdown Extract for Zed

Move selected Markdown into a new file beside the original. The code action
prompts for a filename and inserts a relative Markdown link in the source buffer.
Selections can span lines and include unsaved edits.

## Install from source

Install Rust via rustup and make Python 3.10 or newer with Tk available on your
PATH. To check Tk, run the following command. It opens a small test window.

~~~powershell
python -m tkinter
~~~

In Zed, run `zed: install dev extension` from the command palette and select
this repository's root directory.

## Use it

1. Open a local, named Markdown file in a trusted Zed project.
2. Select the text you want to move.
3. Press **Ctrl+.** (**Cmd+.** on macOS) and choose **Extract selection to Markdown file…**.
4. Enter a filename, such as **Meeting Notes**, and press **OK**. The extension
   adds `.md` if needed and rejects names that already exist.
5. Save the original document to persist the link. The new file is written
   during extraction.

The link label comes from the new filename. For example, **Meeting Notes**
produces `[Meeting Notes](Meeting%20Notes.md)`. The extracted file contains the
selected text, including any selected line breaks. A selection boundary that
includes line breaks keeps those breaks around the inserted link so adjacent
lines stay separate.

If Zed rejects the edit after the new file is written, the new file retains
the selected text. Undo in Zed affects the source edit. The new file persists.

If the action is missing, check that the file is in Markdown mode, text is
selected, and the project is trusted. If you maintain an explicit
Markdown `language_servers` list, add `markdown-extract` to that list. Zed's log
shows language server launch errors.

## Build from source

To compile the Rust adapter separately, run the following from the repository
root.

~~~powershell
rustup target add wasm32-wasip2
cargo build --locked --release --target wasm32-wasip2
~~~

The compiled adapter is `target/wasm32-wasip2/release/markdown_extract.wasm`.
