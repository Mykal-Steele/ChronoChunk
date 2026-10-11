import logging

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
