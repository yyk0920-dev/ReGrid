import random
import pandas as pd

data = []

# F5 = A-G
for _ in range(500):
    A = random.randint(1100, 1500)
    B = random.randint(150, 300)
    C = random.randint(150, 300)
    data.append([A, B, C, "F5"])

# F6 = B-G
for _ in range(500):
    A = random.randint(100, 250)
    B = random.randint(1000, 1400)
    C = random.randint(100, 250)
    data.append([A, B, C, "F6"])

# F7 = C-G
for _ in range(500):
    A = random.randint(40, 150)
    B = random.randint(40, 150)
    C = random.randint(1200, 1700)
    data.append([A, B, C, "F7"])

# N8 = Normal
for _ in range(500):
    A = random.randint(100, 180)
    B = random.randint(100, 180)
    C = random.randint(100, 180)
    data.append([A, B, C, "N8"])

df = pd.DataFrame(data, columns=["A", "B", "C", "label"])
df = df.sample(frac=1).reset_index(drop=True)

df.to_csv("data/regrid_data.csv", index=False)

print("학습 데이터 생성 완료")
print(df.head())