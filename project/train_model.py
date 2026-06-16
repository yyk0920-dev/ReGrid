import os
import joblib
import pandas as pd
import numpy as np

from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
from sklearn.metrics import classification_report
from sklearn.metrics import confusion_matrix

DATA_PATH = "data/regrid_real_data.csv"
MODEL_PATH = "models/random_forest_fault_classifier.pkl"

os.makedirs("models", exist_ok=True)

WINDOW_SIZE = 10

df = pd.read_csv(DATA_PATH)

df = df.dropna()

# -----------------------------
# 기본 feature
# -----------------------------
df["Iab_diff"] = abs(df["Ia"] - df["Ib"])
df["Ibc_diff"] = abs(df["Ib"] - df["Ic"])
df["Ica_diff"] = abs(df["Ic"] - df["Ia"])

df["I_mean"] = (df["Ia"] + df["Ib"] + df["Ic"]) / 3

df["I_unbalance"] = (
    abs(df["Ia"] - df["I_mean"]) +
    abs(df["Ib"] - df["I_mean"]) +
    abs(df["Ic"] - df["I_mean"])
)

df["I_sum"] = df["Ia"] + df["Ib"] + df["Ic"]

# -----------------------------
# Rolling Mean
# -----------------------------
df["Ia_mean_10"] = df["Ia"].rolling(WINDOW_SIZE).mean()
df["Ib_mean_10"] = df["Ib"].rolling(WINDOW_SIZE).mean()
df["Ic_mean_10"] = df["Ic"].rolling(WINDOW_SIZE).mean()

# -----------------------------
# Rolling Variance
# -----------------------------
df["Ia_var_10"] = df["Ia"].rolling(WINDOW_SIZE).var()
df["Ib_var_10"] = df["Ib"].rolling(WINDOW_SIZE).var()
df["Ic_var_10"] = df["Ic"].rolling(WINDOW_SIZE).var()

# -----------------------------
# 변화량 feature
# -----------------------------
df["dIa"] = df["Ia"].diff()
df["dIb"] = df["Ib"].diff()
df["dIc"] = df["Ic"].diff()

df = df.dropna()

features = [
    "Ia",
    "Ib",
    "Ic",
    "temperature",
    "sound",

    "Iab_diff",
    "Ibc_diff",
    "Ica_diff",

    "I_mean",
    "I_unbalance",
    "I_sum",

    "Ia_mean_10",
    "Ib_mean_10",
    "Ic_mean_10",

    "Ia_var_10",
    "Ib_var_10",
    "Ic_var_10",

    "dIa",
    "dIb",
    "dIc"
]

target = "fault_code"

X = df[features]
y = df[target].astype(int)

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42,
    stratify=y
)

model = RandomForestClassifier(
    n_estimators=500,
    max_depth=20,
    min_samples_split=3,
    class_weight="balanced",
    random_state=42
)

model.fit(X_train, y_train)

y_pred = model.predict(X_test)

print("================================")
print("RandomForest 결과")
print("================================")

print("정확도:", accuracy_score(y_test, y_pred))

print()
print(classification_report(y_test, y_pred))

print()
print(confusion_matrix(y_test, y_pred))

# 중요 feature 확인
importance_df = pd.DataFrame({
    "feature": features,
    "importance": model.feature_importances_
})

importance_df = importance_df.sort_values(
    by="importance",
    ascending=False
)

print()
print("중요 feature")
print(importance_df)

joblib.dump(model, MODEL_PATH)

print()
print("모델 저장 완료:", MODEL_PATH)
