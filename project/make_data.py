import os
import random
import pandas as pd

os.makedirs("data", exist_ok=True)

rows = []

def add_data(fault_code, ia_range, ib_range, ic_range, temp_range, spark, count=100):
    for _ in range(count):
        ia = round(random.uniform(*ia_range), 3)
        ib = round(random.uniform(*ib_range), 3)
        ic = round(random.uniform(*ic_range), 3)
        temp = round(random.uniform(*temp_range), 2)

        rows.append({
            "Ia": ia,
            "Ib": ib,
            "Ic": ic,
            "temperature": temp,
            "spark_detected": spark,
            "fault_code": fault_code
        })

# 0 정상
add_data(0, (0.8, 1.5), (0.8, 1.5), (0.8, 1.5), (25, 45), 0)

# F1 3상 단락
add_data(1, (7.0, 10.0), (7.0, 10.0), (7.0, 10.0), (30, 50), 0)

# F2 A-B 단락
add_data(2, (6.0, 9.0), (6.0, 9.0), (0.5, 2.0), (30, 50), 0)

# F3 B-C 단락
add_data(3, (0.5, 2.0), (6.0, 9.0), (6.0, 9.0), (30, 50), 0)

# F4 C-A 단락
add_data(4, (6.0, 9.0), (0.5, 2.0), (6.0, 9.0), (30, 50), 0)

# F5 A상 지락
add_data(5, (4.5, 7.0), (0.5, 2.0), (0.5, 2.0), (30, 50), 0)

# F6 B상 지락
add_data(6, (0.5, 2.0), (4.5, 7.0), (0.5, 2.0), (30, 50), 0)

# F7 C상 지락
add_data(7, (0.5, 2.0), (0.5, 2.0), (4.5, 7.0), (30, 50), 0)

# F8 과열
add_data(8, (0.8, 2.0), (0.8, 2.0), (0.8, 2.0), (70, 100), 0)

# F9 스파크
add_data(9, (0.8, 2.5), (0.8, 2.5), (0.8, 2.5), (30, 60), 1)

df = pd.DataFrame(rows)
df = df.sample(frac=1, random_state=42).reset_index(drop=True)

df.to_csv("data/regrid_data.csv", index=False, encoding="utf-8-sig")

print("regrid_data.csv 생성 완료")
print(df.head())
print(df["fault_code"].value_counts().sort_index())