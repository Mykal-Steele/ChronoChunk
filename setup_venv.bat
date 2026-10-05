@echo off
echo Setting up a new virtual environment for ChronoChunk Discord Bot...

REM Check if venv folder exists and remove it if user confirms
if exist venv (
    echo.
    echo A virtual environment already exists. 
    set /p CONFIRM=Do you want to delete and recreate it? (y/n): 
    if /i "%CONFIRM%"=="y" (
        echo Removing existing virtual environment...
        rmdir /s /q venv
    ) else (
        echo Keeping existing virtual environment.
    )
)

echo Creating new virtual environment...
python -m venv venv

echo Upgrading pip...
venv\Scripts\python.exe -m pip install --upgrade pip

echo Installing discord.py (will be patched later)...
venv\Scripts\pip.exe install discord.py==2.3.2

echo Installing core dependencies...
venv\Scripts\pip.exe install python-dotenv==1.0.0 colorlog==6.7.0 colorama

echo Installing web server and networking...
venv\Scripts\pip.exe install aiohttp==3.9.1 

echo Installing Google AI dependencies...
venv\Scripts\pip.exe install google-generativeai==0.3.2
venv\Scripts\pip.exe install google-api-core google-auth tqdm
venv\Scripts\pip.exe install protobuf==4.25.3 grpcio==1.57.0 grpcio-status==1.57.0

echo Installing helper libraries...
venv\Scripts\pip.exe install typing-extensions aiohappyeyeballs aiosignal attrs frozenlist idna multidict yarl propcache

echo Patching discord.py for Python 3.13...
venv\Scripts\python.exe fix_discord.py

echo.
echo ===========================================
echo Setup complete! Your virtual environment is ready.
echo.
echo To activate the virtual environment:
echo   call venv\Scripts\activate.bat
echo.
echo To run the bot:
echo   call venv\Scripts\activate.bat
echo   python run.py
echo.
echo To deactivate the virtual environment when done:
echo   deactivate
echo ===========================================
echo.

REM Keep window open
pause 