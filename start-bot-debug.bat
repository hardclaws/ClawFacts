@echo off
rem Same as start-bot.bat but with --debug, which prints the exact prompts
rem and responses sent to the LLM (see the bot's --debug flag).
call "%~dp0start-bot.bat" --debug
