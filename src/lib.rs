use zed_extension_api as zed;

struct MarkdownExtract;

impl zed::Extension for MarkdownExtract {
    fn new() -> Self {
        Self
    }

    fn language_server_command(
        &mut self,
        _: &zed::LanguageServerId,
        worktree: &zed::Worktree,
    ) -> zed::Result<zed::Command> {
        let python = worktree
            .which("python")
            .or_else(|| worktree.which("python3"))
            .ok_or("Markdown Extract requires Python 3 with Tk on PATH")?;

        Ok(zed::Command {
            command: python,
            args: vec![
                "-I".into(),
                "-u".into(),
                "-c".into(),
                include_str!("../server.py").into(),
            ],
            env: Default::default(),
        })
    }
}

zed::register_extension!(MarkdownExtract);
