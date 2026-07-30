from dynra_demo.core.math import add
from dynra_demo.core.models import User

# This will hold some live state
active_users = []

def process_new_user(name):
    """Uses core.math and core.models to create a user."""
    u = User(name)
    active_users.append(u)
    # We use 'add' here. If 'add' is hot-patched, 
    # this function should instantly use the new logic.
    score = add(10, 20)
    print(f"Processed {name} with initial score {score}")
    return u

def get_report():
    """Generates a report for all live users."""
    return [u.info() for u in active_users]
