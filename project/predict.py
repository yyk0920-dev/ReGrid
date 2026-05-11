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

if result == "F5":
    print("A-G 고장입니다.")
elif result == "F6":
    print("B-G 고장입니다.")
elif result == "F7":
    print("C-G 고장입니다.")
elif result == "N8":
    print("정상 상태입니다.")