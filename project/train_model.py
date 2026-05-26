import os
import joblib
import pandas as pd

from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

DATA_PATH = "data/regrid_data.csv"
MODEL_PATH = "models/random_forest_model.pkl"

os.makedirs("models", exist_ok=True)

df = pd.read_csv(DATA_PATH)

X = df[["Ia", "Ib", "Ic", "temperature", "spark_detected"]]
y = df["fault_code"]

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42,
    stratify=y
)

model = RandomForestClassifier(
    n_estimators=200,
    max_depth=None,
    random_state=42,
    class_weight="balanced"
)

model.fit(X_train, y_train)

y_pred = model.predict(X_test)

acc = accuracy_score(y_test, y_pred)

print("정확도:", acc)
print()
print("분류 리포트")
print(classification_report(y_test, y_pred))
print()
print("혼동 행렬")
print(confusion_matrix(y_test, y_pred))

joblib.dump(model, MODEL_PATH)

print()
print(f"모델 저장 완료: {MODEL_PATH}")