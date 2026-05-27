import joblib
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix, classification_report

MODEL_PATH = "models/random_forest_fault_classifier.pkl"
DATA_PATH = "data/regrid_real_data.csv"

fault_names = {
    0: "NORMAL / 정상",
    1: "F1 / 3상 단락",
    2: "F2 / A-B 단락",
    3: "F3 / B-C 단락",
    4: "F4 / C-A 단락",
    5: "F5 / A상 지락",
    6: "F6 / B상 지락",
    7: "F7 / C상 지락",
    8: "F8 / 과열",
    9: "F9 / 스파크"
}

features = ["Ia", "Ib", "Ic", "temperature", "sound"]

model = joblib.load(MODEL_PATH)
df = pd.read_csv(DATA_PATH).dropna()

X = df[features]
y = df["fault_code"].astype(int)
pred = model.predict(X)

labels = sorted(y.unique())
target_names = [fault_names[i] for i in labels]

print("================================")
print("전체 데이터 기준 예측 확인")
print("================================")
print("전체 데이터 개수:", len(df))
print("accuracy:", accuracy_score(y, pred))
print()

print("라벨별 개수")
print(y.value_counts().sort_index())
print()

print("classification report")
print(classification_report(
    y,
    pred,
    labels=labels,
    target_names=target_names
))

print("confusion matrix")
print(confusion_matrix(y, pred, labels=labels))

print()
print("================================")
print("라벨별 평균값")
print("================================")
print(df.groupby("fault_code")[features].mean())

print()
print("================================")
print("라벨별 표준편차")
print("================================")
print(df.groupby("fault_code")[features].std())

print()
print("================================")
print("라벨별 샘플 5개 예측")
print("================================")

for label in labels:
    label_df = df[df["fault_code"] == label]

    sample = label_df.sample(
        n=min(5, len(label_df)),
        random_state=42
    )

    sample_pred = model.predict(sample[features])

    print()
    print(f"[실제 {label}] {fault_names.get(label, 'UNKNOWN')}")

    for idx, row in enumerate(sample.itertuples()):
        p = int(sample_pred[idx])

        print(
            f"Ia={row.Ia:.3f}, "
            f"Ib={row.Ib:.3f}, "
            f"Ic={row.Ic:.3f}, "
            f"T={row.temperature:.2f}, "
            f"S={row.sound:.2f} "
            f"=> 예측 {p} {fault_names.get(p, 'UNKNOWN')}"
        )
