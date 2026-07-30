from finance_app.models.account import Account
from finance_app.core.engine import calculate_interest

x = 1
y = 1

def foo(x):
    return x

def run_app():
    acc = Account("Alice", 1000)
    acc
    interest = calculate_interest(acc.balance, 0.05)
    print(f"Initial State: {acc}, Expected Interest: {interest}")
    return acc

if __name__ == "__main__":
    alice_acc = run_app()
    alice_acc

run_app()
    
