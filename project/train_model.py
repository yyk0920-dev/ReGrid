import os
import glob
import joblib
import numpy as np
import pandas as pd

from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

DATA_DIR = "data"
MODEL_DIR = "models"
MODEL_PATH = os.path.join(MODEL_DIR, "regrid_fault_model.pkl")

os.makedirs(MODEL_DIR, exist_ok=True)


def add_features(df):
    eps = 1e-6

    # 기본 전류 절댓값
    df["Ia_abs"] = df["Ia"].abs()
    df["Ib_abs"] = df["Ib"].abs()
    df["Ic_abs"] = df["Ic"].abs()

    # 전류 통계값
    df["I_sum"] = df["Ia_abs"] + df["Ib_abs"] + df["Ic_abs"]
    df["I_mean"] = df[["Ia_abs", "Ib_abs", "Ic_abs"]].mean(axis=1)
    df["I_max"] = df[["Ia_abs", "Ib_abs", "Ic_abs"]].max(axis=1)
    df["I_min"] = df[["Ia_abs", "Ib_abs", "Ic_abs"]].min(axis=1)
    df["I_range"] = df["I_max"] - df["I_min"]
    df["I_std"] = df[["Ia_abs", "Ib_abs", "Ic_abs"]].std(axis=1)

    # 각 상 비율
    df["Ia_ratio"] = df["Ia_abs"] / (df["I_sum"] + eps)
    df["Ib_ratio"] = df["Ib_abs"] / (df["I_sum"] + eps)
    df["Ic_ratio"] = df["Ic_abs"] / (df["I_sum"] + eps)

    # 상끼리 차이
    df["Iab_diff"] = (df["Ia_abs"] - df["Ib_abs"]).abs()
    df["Ibc_diff"] = (df["Ib_abs"] - df["Ic_abs"]).abs()
    df["Ica_diff"] = (df["Ic_abs"] - df["Ia_abs"]).abs()

    # 불평형 정도
    df["imbalance"] = df["I_range"] / (df["I_mean"] + eps)

    return df


def main():
    csv_files = sorted(glob.glob(os.path.join(DATA_DIR, "*.csv")))

    if len(csv_files) == 0:
        print("data 폴더에 CSV 파일이 없음")
        return

    print("읽은 CSV 파일:")
    for file in csv_files:
        print(" -", file)

    df_list = []

    for file in csv_files:
        temp = pd.read_csv(file)

        if len(temp) == 0:
            print(f"비어있는 파일이라 제외: {file}")
            continue

        df_list.append(temp)

    if len(df_list) == 0:
        print("사용 가능한 CSV 데이터가 없음")
        return

    df = pd.concat(df_list, ignore_index=True)

    print("\n전체 데이터 개수:", len(df))

    required_cols = [
        "fault_code",
        "fault_name",
        "Ia",
        "Ib",
        "Ic",
        "temperature",
        "sound"
    ]

    for col in required_cols:
        if col not in df.columns:
            print(f"필수 컬럼 없음: {col}")
            return

    # 숫자 변환
    numeric_cols = ["fault_code", "Ia", "Ib", "Ic", "temperature", "sound"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # 이상한 값 제거
    df = df.dropna(subset=numeric_cols)
    df["fault_code"] = df["fault_code"].astype(int)

    print("\n라벨별 데이터 개수:")
    print(df["fault_code"].value_counts().sort_index())

    print("\n라벨 이름:")
    label_table = df[["fault_code", "fault_name"]].drop_duplicates().sort_values("fault_code")
    print(label_table.to_string(index=False))

    # feature 생성
    df = add_features(df)

    feature_cols = [
        "Ia", "Ib", "Ic",
        "Ia_abs", "Ib_abs", "Ic_abs",
        "I_sum", "I_mean", "I_max", "I_min", "I_range", "I_std",
        "Ia_ratio", "Ib_ratio", "Ic_ratio",
        "Iab_diff", "Ibc_diff", "Ica_diff",
        "imbalance",
        "temperature", "sound"
    ]

    X = df[feature_cols]
    y = df["fault_code"]

    # train/test 분리
    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.2,
        random_state=42,
        stratify=y
    )

    print("\n학습 데이터 개수:", len(X_train))
    print("테스트 데이터 개수:", len(X_test))

    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=None,
        random_state=42,
        n_jobs=-1,
        class_weight="balanced"
    )

    print("\n모델 학습 중...")
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)

    acc = accuracy_score(y_test, y_pred)

    print("\n====================================")
    print("학습 결과")
    print("====================================")
    print(f"정확도: {acc:.4f}")

    print("\nclassification report:")
    print(classification_report(y_test, y_pred))

    labels = sorted(df["fault_code"].unique())
    cm = confusion_matrix(y_test, y_pred, labels=labels)

    cm_df = pd.DataFrame(
        cm,
        index=[f"true_{x}" for x in labels],
        columns=[f"pred_{x}" for x in labels]
    )

    print("\nconfusion matrix:")
    print(cm_df)

    importances = model.feature_importances_
    importance_df = pd.DataFrame({
        "feature": feature_cols,
        "importance": importances
    }).sort_values("importance", ascending=False)

    print("\nfeature importance TOP 10:")
    print(importance_df.head(10).to_string(index=False))

    # fault_code -> fault_name 저장
    label_names = (
        df[["fault_code", "fault_name"]]
        .drop_duplicates()
        .sort_values("fault_code")
        .set_index("fault_code")["fault_name"]
        .to_dict()
    )

    save_data = {
        "model": model,
        "feature_cols": feature_cols,
        "label_names": label_names
    }

    joblib.dump(save_data, MODEL_PATH)

    print("\n====================================")
    print("모델 저장 완료")
    print(f"저장 경로: {MODEL_PATH}")
    print("====================================")


if __name__ == "__main__":
    main()
