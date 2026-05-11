import random
import pandas as pd

data = []

def add_data(count, a_range, b_range, c_range, label):
    for _ in range(count):
        A = random.randint(a_range[0], a_range[1])
        B = random.randint(b_range[0], b_range[1])
        C = random.randint(c_range[0], c_range[1])
        data.append([A, B, C, label])

# F1 = 3상 A-B-C 고장
add_data(1000, (450, 2000), (450, 2000), (450, 2000), "F1")

# F2 = 2상 A-B 고장
add_data(1000, (450, 1700), (450, 1700), (100, 449), "F2")

# F3 = 2상 B-C 고장
add_data(1000, (100, 449), (450, 1400), (450, 1400), "F3")

# F4 = 2상 C-A 고장
add_data(1000, (450, 1400), (100, 449), (450, 1400), "F4")

# F5 = 1선 지락 A-G
add_data(1000, (450, 1500), (100, 449), (100, 449), "F5")

# F6 = 1선 지락 B-G
add_data(1000, (100, 449), (450, 1400), (100, 449), "F6")

# F7 = 1선 지락 C-G
add_data(1000, (100, 449), (100, 449), (450, 1700), "F7")

# N8 = 정상
add_data(1000, (100, 449), (100, 449), (100, 449), "N8")

df = pd.DataFrame(data, columns=["A", "B", "C", "label"])
df = df.sample(frac=1).reset_index(drop=True)

df.to_csv("data/regrid_data.csv", index=False)

print("학습 데이터 생성 완료")
print(df.head())