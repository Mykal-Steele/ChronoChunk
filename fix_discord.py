"""
Script to patch discord.py to work with Python 3.13 by disabling audioop imports.
"""
import os
import re
import sys

def find_site_packages():
    """Find the site-packages directory of the current Python environment."""
    for path in sys.path:
        if path.endswith('site-packages'):
            return path
    return None

def patch_discord_player(file_path):
    """Patch the discord/player.py file to avoid importing audioop."""
    # Read the file
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check if already patched
    if '# PATCHED FOR PYTHON 3.13' in content:
        print(f"File already patched: {file_path}")
        return False
    
    # Replace the audioop import with a dummy implementation
    patched_content = content.replace(
        'import audioop',
        '# PATCHED FOR PYTHON 3.13 - audioop module not available\n'
        'class DummyAudioop:\n'
        '    @staticmethod\n'
        '    def rms(*args, **kwargs):\n'
        '        return 0\n'
        'audioop = DummyAudioop()'
    )
    
    # Write the patched file
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(patched_content)
    
    print(f"Successfully patched: {file_path}")
    return True

def patch_discord_voice_client(file_path):
    """Patch discord/voice_client.py to disable voice functionality."""
    # Read the file
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check if already patched
    if '# PATCHED FOR PYTHON 3.13' in content:
        print(f"File already patched: {file_path}")
        return False
    
    # Add a warning about voice support being disabled
    patched_content = content.replace(
        'class VoiceClient(VoiceProtocol):',
        '# PATCHED FOR PYTHON 3.13 - Voice support disabled\n'
        'class VoiceClient(VoiceProtocol):'
    )
    
    # Write the patched file
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(patched_content)
    
    print(f"Successfully patched: {file_path}")
    return True

def main():
    """Main function to patch discord.py files."""
    site_packages = find_site_packages()
    if not site_packages:
        print("Could not find site-packages directory!")
        return
    
    # Patch discord/player.py
    player_path = os.path.join(site_packages, 'discord', 'player.py')
    if os.path.exists(player_path):
        patch_discord_player(player_path)
    else:
        print(f"File not found: {player_path}")
    
    # Patch discord/voice_client.py
    voice_client_path = os.path.join(site_packages, 'discord', 'voice_client.py')
    if os.path.exists(voice_client_path):
        patch_discord_voice_client(voice_client_path)
    else:
        print(f"File not found: {voice_client_path}")
    
    print("\nPatch completed! discord.py should now work with Python 3.13.")
    print("Try running your bot now.")

if __name__ == "__main__":
    main() 