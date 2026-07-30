class User:
    def __init__(self, username, status="online"):
        self.username = username
        self.status = status
        self.version = 1

    def info(self):
        return f"User({self.username}, status={self.status}, v={self.version})"

    def __dynra_update__(self):
        """Automatically called when the class is redefined."""
        print(f"Updating instance of {self.username}...")
        if not hasattr(self, 'version'):
            self.version = 1
        # Example migration: bump version
        self.version += 1
