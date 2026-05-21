import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
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
    X,
    y,
    test_size=0.2,
    random_state=42
)

# RandomForest 모델 생성
model = RandomForestClassifier(
    n_estimators=100,   # 결정트리 개수
    max_depth=None,     # 트리 깊이 제한 없음
    random_state=42,
    n_jobs=-1           # CPU 코어 최대 사용
)

# 학습
model.fit(X_train, y_train)

# 테스트
pred = model.predict(X_test)

# 정확도 출력
accuracy = accuracy_score(y_test, pred)
print("정확도:", accuracy)

# 모델 저장
with open("models/regrid_model.pkl", "wb") as f:
    pickle.dump(model, f)

print("모델 저장 완료: models/regrid_model.pkl")