import logging
import sys

class SafeStreamHandler(logging.StreamHandler):
    """Stream handler that safely handles Unicode characters in Windows console"""
    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            # Replace problematic Unicode characters with ASCII equivalents
            msg = msg.replace('→', '->')
            # Handle other potential Unicode characters
            if getattr(stream, 'encoding', None) is not None:
                msg = msg.encode(stream.encoding, 'replace').decode(stream.encoding)
            stream.write(msg + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)

def patch_all_loggers():
    """Replace all existing StreamHandlers with SafeStreamHandlers"""
    # First, patch the root logger
    root_logger = logging.getLogger()
    for i, handler in enumerate(root_logger.handlers[:]):
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, SafeStreamHandler):
            # Create a new safe handler
            new_handler = SafeStreamHandler(handler.stream)
            new_handler.setLevel(handler.level)
            new_handler.setFormatter(handler.formatter)
            # Remove old handler and add new one
            root_logger.removeHandler(handler)
            root_logger.addHandler(new_handler)
    
    # Then, patch all other loggers
    for name in logging.root.manager.loggerDict:
        logger = logging.getLogger(name)
        for i, handler in enumerate(logger.handlers[:]):
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, SafeStreamHandler):
                # Create a new safe handler
                new_handler = SafeStreamHandler(handler.stream)
                new_handler.setLevel(handler.level)
                new_handler.setFormatter(handler.formatter)
                # Remove old handler and add new one
                logger.removeHandler(handler)
                logger.addHandler(new_handler)