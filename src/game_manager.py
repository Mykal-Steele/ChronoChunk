import random
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from config.config import Config
from src.logger import logger

@dataclass
class GameState:
    secret_number: int
    attempts_left: int
    max_range: int

class GameManager:
    def __init__(self):
        # keep track of active games by user id
        self.active_games: Dict[int, GameState] = {}
        
    def start_game(self, user_id: int, max_range: int) -> Tuple[bool, str]:
        """Start a new game for a user"""
        
        # check if they already playing
        if user_id in self.active_games:
            return False, "You already got a game going! Finish it or use /end first."
            
        # make sure input ain't dumb
        if max_range < 1:
            return False, "Bruh give me a number bigger than 1 💀"
            
        # set up new game
        secret = random.randint(1, max_range)
        self.active_games[user_id] = GameState(
            secret_number=secret,
            attempts_left=Config.MAX_GAME_ATTEMPTS,
            max_range=max_range
        )
        
        logger.info(f"Started new game for user {user_id} with range 1-{max_range}")
        return True, f"Game Started! I'm thinking of a number between 1 and {max_range}. Start guessing with /guess <your number>. You got {Config.MAX_GAME_ATTEMPTS} attempts."
        
    def end_game(self, user_id: int) -> Tuple[bool, str]:
        """End a user's game"""
        if user_id in self.active_games:
            del self.active_games[user_id]
            logger.info(f"Ended game for user {user_id}")
            return True, "gg thanks for playing"
        return False, "You don't even have a game going rn"
        
    def make_guess(self, user_id: int, guess: int) -> Tuple[bool, str]:
        """Handle a user's guess"""
        
        # check if they playing
        if user_id not in self.active_games:
            return False, "You don't have a game going. Start one with /game <max_range>"
            
        game = self.active_games[user_id]
        
        # they got it!
        if guess == game.secret_number:
            del self.active_games[user_id]
            logger.info(f"User {user_id} won their game!")
            return True, f"YOOO YOU GOT IT! The number was {game.secret_number} 🔥"
            
        # wrong guess
        game.attempts_left -= 1
        
        if game.attempts_left > 0:
            # give em a hint
            hint = "higher" if game.secret_number > guess else "lower"
            return False, f"Nah that ain't it. You got {game.attempts_left} tries left! The number is {hint} than {guess}"
            
        # game over
        del self.active_games[user_id]
        logger.info(f"User {user_id} lost their game")
        return False, f"RIP GAME OVER! The number was {game.secret_number}. Better luck next time 💀"
        
    def get_active_game(self, user_id: int, channel_id: str = None) -> Optional[GameState]:
        """Get a user's current game state if they have one"""
        return self.active_games.get(user_id)

    async def guess_word(self, user_id: str, channel_id: str, guess: str) -> str:
        """Process a guess for the currently active game"""
        # Convert user_id to int to match the internal representation
        user_id_int = int(user_id) if user_id.isdigit() else 0
        game = self.get_active_game(user_id_int, channel_id)
        if not game:
            return "No active game found. Start one with /game"
            
        guess = guess.strip().lower()
        
        # Check if this is a word guessing game - we need to adapt the structure
        # The word guessing feature seems incomplete, just return a placeholder for now
        # as the GameState doesn't have a target_word attribute
        return "Word guessing game is coming soon!"
        
        # The following code won't work with the current GameState structure
        # Left here for reference for future implementation
        """
        target = game["target_word"].lower()
        
        # dynamic difficulty scaling based on word length - more guesses for harder words
        # this feels way more fair than a fixed number of attempts for all words
        max_attempts = min(10, max(6, len(target) - 1))
        
        # if they already used all their guesses, let them know
        if len(game["guesses"]) >= max_attempts:
            return f"You've used all {max_attempts} guesses. The word was '{target}'."
            
        # already guessed this exact word
        if guess in game["guesses"]:
            return f"You already guessed '{guess}'. Try something else!"
            
        game["guesses"].append(guess)
        """ 