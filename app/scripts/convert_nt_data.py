import pandas as pd
import sys

input_file = sys.argv[1]
output_file = input_file.replace(".txt", ".csv")

df = pd.read_csv(
    input_file,
    sep=";",
    header=None,
    names=["datetime", "open", "high", "low", "close", "volume"]
)

# Parse timestamp
df["timestamp"] = pd.to_datetime(
    df["datetime"],
    format="%Y%m%d %H%M%S"
)

df = df[["timestamp", "open", "high", "low", "close", "volume"]]

df.to_csv(output_file, index=False)

print(f"Converted file saved to: {output_file}")
