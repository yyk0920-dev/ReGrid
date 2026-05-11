import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeClassifier
from sklearn.metrics import accuracy_score
import pickle

# 데이터 읽기
df = pd.read_csv("data/regrid_data.csv")

# 입력값
X = df[["A", "B", "C"]]

# 정답값
y = df["label"]

# 학습용 / 테스트용 나누기
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)

# AI 모델 생성
model = DecisionTreeClassifier()

# 학습
model.fit(X_train, y_train)

# 테스트
pred = model.predict(X_test)

accuracy = accuracy_score(y_test, pred)
print("정확도:", accuracy)

# 모델 저장
with open("models/regrid_model.pkl", "wb") as f:
    pickle.dump(model, f)

print("모델 저장 완료: models/regrid_model.pkl")