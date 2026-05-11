import pickle
import pandas as pd

with open("models/regrid_model.pkl", "rb") as f:
    model = pickle.load(f)

A = int(input("A상 전류 입력: "))
B = int(input("B상 전류 입력: "))
C = int(input("C상 전류 입력: "))

X = pd.DataFrame([[A, B, C]], columns=["A", "B", "C"])

result = model.predict(X)[0]

print("판정 결과:", result)

descriptions = {
    "F1": "3상 단락 고장 A-B-C",
    "F2": "2상 단락 고장 A-B",
    "F3": "2상 단락 고장 B-C",
    "F4": "2상 단락 고장 C-A",
    "F5": "1선 지락 고장 A-G",
    "F6": "1선 지락 고장 B-G",
    "F7": "1선 지락 고장 C-G",
    "N8": "정상 상태"
}

print(descriptions[result])
