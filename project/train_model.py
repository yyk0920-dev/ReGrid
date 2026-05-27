import os
import joblib
import pandas as pd

from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

DATA_PATH = "data/regrid_real_data.csv"
MODEL_PATH = "models/random_forest_fault_classifier.pkl"

os.makedirs("models", exist_ok=True)

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

df = pd.read_csv(DATA_PATH)
df = df.dropna()

features = ["Ia", "Ib", "Ic", "temperature", "sound"]
target = "fault_code"

X = df[features]
y = df[target].astype(int)

print("================================")
print("학습 데이터 확인")
print("================================")
print("전체 데이터 개수:", len(df))
print()
print("라벨별 개수:")
print(y.value_counts().sort_index())
print()
print("라벨별 평균값:")
print(df.groupby("fault_code")[features].mean())
print()
print("라벨별 표준편차:")
print(df.groupby("fault_code")[features].std())
print()

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42,
    stratify=y
)

model = RandomForestClassifier(
    n_estimators=300,
    random_state=42,
    class_weight="balanced"
)

model.fit(X_train, y_train)

y_pred = model.predict(X_test)

labels = sorted(y.unique())
target_names = [fault_names[i] for i in labels]

print("================================")
print("Random Forest 고장 유형 분류 결과")
print("================================")
print("정확도:", accuracy_score(y_test, y_pred))
print()
print("분류 리포트")
print(classification_report(
    y_test,
    y_pred,
    labels=labels,
    target_names=target_names
))
print()
print("혼동 행렬")
print(confusion_matrix(y_test, y_pred, labels=labels))

joblib.dump(model, MODEL_PATH)

print()
print("모델 저장 완료:", MODEL_PATH)
