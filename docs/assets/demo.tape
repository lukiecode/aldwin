# VHS tape for Aldwin 0.0.1 terminal demo (docs/assets/demo.gif)
# Run with: vhs docs/assets/demo.tape

Output docs/assets/demo.gif

Set Shell "zsh"
Set FontSize 15
Set Width 960
Set Height 540
Set Padding 20
Set TypingSpeed 40ms
Set PlaybackSpeed 1.0

Type "uv tool install aldwin"
Enter
Sleep 1.5s

Type "aldwin"
Enter
Sleep 1.5s

# 1. Onboarding: paste OpenAI key (hidden), skip Tavily, accept default model
Type "sk-proj-demo1234567890abcd"
Enter
Sleep 1.2s
Enter
Sleep 0.8s
Enter
Sleep 1.5s

# 2. Ask a task that triggers run_command approval and memory
Type "Check how much free disk space I have and remember it for today"
Enter
Sleep 2.5s

# 3. Approve the `df -h` command prompt with `y`
Type "y"
Sleep 4s

# 4. Exit cleanly
Type "/exit"
Enter
Sleep 1s
