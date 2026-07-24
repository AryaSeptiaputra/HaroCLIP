class RenderingError(Exception):
    def __init__(self, message: str, stage: str):
        super().__init__(message)
        self.message = message
        self.stage = stage
