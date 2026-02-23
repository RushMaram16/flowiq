from engine.data_loader import load_data
from engine.optimizer import optimize_itinerary

# Load dataset
data = load_data("C:\Users\smvk2\OneDrive\Desktop\Trip_optimizer\engine\phase1_data.xlsx")

# Example test query
attractions = ["Prado Museum", "Retiro Park", "Royal Palace"]

result = optimize_itinerary(
    attractions=attractions,
    start_location="Madrid Centro"
)

print(result)
