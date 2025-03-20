@echo off
echo Installing and patching discord.py for Python 3.13...

echo Upgrading pip...
pip install --upgrade pip

echo Installing discord.py (will be patched later)...
pip install discord.py==2.3.2

echo Installing other dependencies...
pip install python-dotenv==1.0.0 colorlog==6.7.0 colorama
pip install aiohttp==3.9.1
pip install google-generativeai==0.3.2
pip install google-api-core google-auth tqdm
pip install protobuf==4.25.3 grpcio==1.57.0
pip install typing-extensions aiohappyeyeballs aiosignal attrs

echo Patching discord.py for Python 3.13...
python fix_discord.py

echo.
echo Installation and patching complete! Your bot should now work with Python 3.13.
echo Try running: python run.py
echo.

pause 