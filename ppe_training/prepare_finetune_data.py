"""
Build the data for fine-tuning the PPE detector on Construction-PPE.

Class space stays the production model's six classes (boots, gloves, helmet,
mask, person, safety_vest) so the trained weights are a drop-in replacement and
fine-tuning continues from the current model. Construction-PPE labels are mapped:

    helmet -> helmet      vest -> safety_vest     Person -> person
    gloves -> gloves      boots -> boots
    goggles, no_helmet, no_vest, no_gloves, no_boots, no_goggle, forklift -> dropped

(The "no_*" labels are NOT trained on: the product infers absence from the
absence of a detection, and an explicit absence class scored far worse in this
project's earlier experiment. They are only used for evaluation.)

Splits (no test split exists upstream, and picking the best epoch on the images
used for the final number would flatter it, so the 200 upstream validation images
are halved by file order):

    train      the 800 Construction-PPE train images, listed TWICE per epoch
               (so the new domain is not drowned out) + a random 1,600 of the
               4,428 images the current model was trained on (so it does not
               forget them)
    valA       100 val images: used during training to pick the best epoch
    valB       100 val images: never seen in training or selection, used for the
               final comparison with the current model

Writes ppe_training/datasets/ppe_finetune/ (git-ignored) and data.yaml.

Run:  venv/Scripts/python.exe ppe_training/prepare_finetune_data.py
"""

import glob
import os
import random
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "datasets", "construction_ppe")
OLD = os.path.join(HERE, "datasets", "ppe6_roboflow")
OUT = os.path.join(HERE, "datasets", "ppe_finetune")

# production class ids
NAMES = ["boots", "gloves", "helmet", "mask", "person", "safety_vest"]
CONSTRUCTION_TO_PROD = {0: 2, 1: 1, 2: 5, 3: 0, 5: 4}      # helmet, gloves, vest, boots, Person
OLD_KEEP = {0, 1, 2, 3, 4, 5}                              # unchanged
OLD_SUBSET = 1600
SEED = 7


def link_or_copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def write_labels(rows, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(" ".join(r) + "\n")


def remap(label_path, mapping):
    rows = []
    for line in open(label_path, encoding="utf-8"):
        p = line.split()
        if len(p) == 5 and int(p[0]) in mapping:
            rows.append([str(mapping[int(p[0])])] + p[1:])
    return rows


def add_set(name, images, label_for, mapping):
    out_paths = []
    for img in images:
        base = os.path.basename(img)
        dst_img = os.path.join(OUT, "images", name, base)
        link_or_copy(img, dst_img)
        write_labels(remap(label_for(img), mapping), os.path.join(OUT, "labels", name, base[:-4] + ".txt"))
        out_paths.append(dst_img)
    return out_paths


def main():
    random.seed(SEED)
    if not os.path.isdir(os.path.join(SRC, "images")):
        raise SystemExit("download the Construction-PPE images first (download_construction_ppe.py)")

    def con_label(img):
        return img.replace(os.sep + "images" + os.sep, os.sep + "labels" + os.sep)[:-4] + ".txt"

    train_c = sorted(glob.glob(os.path.join(SRC, "images", "train", "*.jpg")))
    val_c = sorted(glob.glob(os.path.join(SRC, "images", "val", "*.jpg")))
    val_a, val_b = val_c[0::2], val_c[1::2]

    p_train_c = add_set("train_construction", train_c, con_label, CONSTRUCTION_TO_PROD)
    p_val_a = add_set("valA", val_a, con_label, CONSTRUCTION_TO_PROD)
    p_val_b = add_set("valB", val_b, con_label, CONSTRUCTION_TO_PROD)

    old = sorted(glob.glob(os.path.join(OLD, "train", "images", "*.jpg")))
    old = random.sample(old, min(OLD_SUBSET, len(old)))
    p_old = add_set(
        "train_ppe6", old,
        lambda img: img.replace(os.sep + "images" + os.sep, os.sep + "labels" + os.sep)[:-4] + ".txt",
        {i: i for i in OLD_KEEP})

    train_list = os.path.join(OUT, "train.txt")
    with open(train_list, "w", encoding="utf-8") as f:
        for p in p_train_c * 2 + p_old:
            f.write(p.replace("\\", "/") + "\n")
    for name, paths in (("valA", p_val_a), ("valB", p_val_b)):
        with open(os.path.join(OUT, f"{name}.txt"), "w", encoding="utf-8") as f:
            for p in paths:
                f.write(p.replace("\\", "/") + "\n")

    with open(os.path.join(OUT, "data.yaml"), "w", encoding="utf-8") as f:
        f.write(f"path: {OUT.replace(chr(92), '/')}\ntrain: train.txt\nval: valA.txt\n")
        f.write(f"nc: {len(NAMES)}\nnames: {NAMES}\n")
    with open(os.path.join(OUT, "data_valB.yaml"), "w", encoding="utf-8") as f:
        f.write(f"path: {OUT.replace(chr(92), '/')}\ntrain: train.txt\nval: valB.txt\n")
        f.write(f"nc: {len(NAMES)}\nnames: {NAMES}\n")
    print(f"train: {len(p_train_c)} construction x2 + {len(p_old)} old = {len(p_train_c) * 2 + len(p_old)} per epoch")
    print(f"valA {len(p_val_a)}  valB {len(p_val_b)}")


if __name__ == "__main__":
    main()
