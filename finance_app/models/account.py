class Account:
    def __init__(self, owner, balance=0):
        self.owner = owner
        self.balance = balance

    def deposit(self, amount):
        self.balance += amount
        return self.balance

    def apply_fee(self):
        # We will patch this logic live!
        fee = 5
        self.balance -= fee
        return f"Fee of {fee} applied. New balance: {self.balance}"

    def __repr__(self):
        return f"Account({self.owner}, {self.balance})"
