import os
import joblib
import pandas as pd

from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

DATA_PATH = "data/regrid_data.csv"
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

features = ["Ia", "Ib", "Ic", "temperature", "spark_detected"]
target = "fault_code"

X = df[features]
y = df[target]

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42,
    stratify=y
)

model = RandomForestClassifier(
    n_estimators=200,
    random_state=42,
    class_weight="balanced"
)

model.fit(X_train, y_train)

y_pred = model.predict(X_test)

print("================================")
print("Random Forest 고장 유형 분류 결과")
print("================================")
print("정확도:", accuracy_score(y_test, y_pred))
print()
print("분류 리포트")
print(classification_report(y_test, y_pred, target_names=[fault_names[i] for i in range(10)]))
print()
print("혼동 행렬")
print(confusion_matrix(y_test, y_pred))

joblib.dump(model, MODEL_PATH)

print()
print("모델 저장 완료:", MODEL_PATH)