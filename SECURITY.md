# Private configuration and responsible use

Create your own Telegram bot and AI credentials. Keep `.env`, runtime settings,
voice assets, browser profiles, databases, logs, and conversation history out of
Git. Ignore rules exclude these files. Only examples and synthetic test fixtures
belong in the repository.

Allow only your own numeric Telegram user ID in a private chat. Treat the bot
token as a password. Revoke and replace exposed tokens/keys at their provider;
deleting a commit does not revoke a credential.

Mika can run commands and interact with applications under your Windows account.
Use a standard Windows user. Approval checks are on by default; trusted-owner
mode is an explicit local choice. Keep model servers and browser debugging ports
on loopback. Do not expose runtime directories or credentials to other machines.
Local AI does not make Telegram offline.

Cloud providers may receive requested files, browser content, and screenshots
needed for a task. Text-only models require owner approval when visual review
is necessary. Login, CAPTCHA, Windows UAC, and locked desktops remain controlled
by the user. External content cannot authorize actions or change permissions.

Share minimal reproductions and redacted diagnostics. Do not post live tokens,
personal inbox content, private screenshots, or raw runtime logs in issues.
Use only voice styles/recordings you are entitled to use, and review upstream
model and voice licenses before redistribution.
