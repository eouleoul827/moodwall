"""KcELECTRA 감정 분류 파인튜닝 (Colab GPU에서 실행)

사용법 (Colab):
    !pip install transformers datasets scikit-learn accelerate
    !python train.py --data data/train.csv

data/train.csv 형식 (헤더 필수):
    text,label
    민수야 생일 축하해!!,축하
    시험 끝나서 너무 좋다ㅋㅋ,기쁨
"""
import argparse

import numpy as np
import pandas as pd
from datasets import Dataset
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.model_selection import train_test_split
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    Trainer,
    TrainingArguments,
)

from labels import ID2LABEL, LABEL2ID, LABELS

BASE_MODEL = "beomi/KcELECTRA-base-v2022"
MAX_LEN = 64  # 멘션이 30자 제한이라 64토큰이면 충분


def load_data(path):
    df = pd.read_csv(path).dropna(subset=["text", "label"])
    df["text"] = df["text"].astype(str).str.strip()
    df["label"] = df["label"].astype(str).str.strip()

    unknown = set(df["label"]) - set(LABELS)
    if unknown:
        raise ValueError(f"라벨 목록에 없는 라벨이 있어요: {unknown}")

    df = df.drop_duplicates(subset=["text"])
    df["labels"] = df["label"].map(LABEL2ID)

    print("라벨별 개수")
    print(df["label"].value_counts(), "\n")
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/train.csv")
    parser.add_argument("--out", default="model")
    parser.add_argument("--epochs", type=int, default=5)
    args = parser.parse_args()

    df = load_data(args.data)

    # 학습 80% / 검증 10% / 테스트 10% (라벨 비율 유지)
    train_df, temp_df = train_test_split(df, test_size=0.2, stratify=df["labels"], random_state=42)
    val_df, test_df = train_test_split(temp_df, test_size=0.5, stratify=temp_df["labels"], random_state=42)
    test_df.to_csv("data/test_split.csv", index=False)  # 판정 기준값 조정할 때 사용

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)

    def tokenize(batch):
        return tokenizer(batch["text"], truncation=True, max_length=MAX_LEN)

    def to_ds(d):
        return Dataset.from_pandas(d[["text", "labels"]].reset_index(drop=True)).map(tokenize, batched=True)

    train_ds, val_ds, test_ds = to_ds(train_df), to_ds(val_df), to_ds(test_df)

    model = AutoModelForSequenceClassification.from_pretrained(
        BASE_MODEL, num_labels=len(LABELS), id2label=ID2LABEL, label2id=LABEL2ID
    )

    def compute_metrics(pred):
        preds = np.argmax(pred.predictions, axis=-1)
        return {
            "accuracy": accuracy_score(pred.label_ids, preds),
            "macro_f1": f1_score(pred.label_ids, preds, average="macro"),
        }

    training_args = TrainingArguments(
        output_dir="checkpoints",
        num_train_epochs=args.epochs,
        learning_rate=5e-5,
        per_device_train_batch_size=32,
        per_device_eval_batch_size=64,
        weight_decay=0.01,
        warmup_ratio=0.1,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="macro_f1",
        save_total_limit=1,
        logging_steps=50,
        report_to="none",
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        processing_class=tokenizer,
        compute_metrics=compute_metrics,
    )
    trainer.train()

    # 테스트 데이터로 최종 성능 확인 (발표 결과에 사용)
    pred = trainer.predict(test_ds)
    preds = np.argmax(pred.predictions, axis=-1)
    print("\n[테스트 결과]")
    print(classification_report(pred.label_ids, preds, labels=list(range(len(LABELS))),
                                target_names=LABELS, zero_division=0))

    trainer.save_model(args.out)
    tokenizer.save_pretrained(args.out)
    print(f"\n모델 저장 완료: {args.out}/  (이 폴더를 서버로 옮기면 됨)")


if __name__ == "__main__":
    main()
