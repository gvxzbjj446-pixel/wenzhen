"""配伍禁忌（十八反、十九畏）提示。

只做提示，不阻止保存：是否同用由医师决定。
规则用正则表达式描述，同一份规则也传给前端，在录入处方时实时提示。
"""

import re

# (类别, 说明, 甲类药名模式, 乙类药名模式)：处方中甲、乙两类同时出现即提示
RULES = (
    ("十八反", "甘草反甘遂、大戟、海藻、芫花",
     ("甘草",), ("甘遂", "大戟", "海藻", "芫花")),
    ("十八反", "乌头类（川乌、草乌、附子）反半夏、瓜蒌、天花粉、贝母、白蔹、白及",
     ("乌头", "川乌", "草乌", "(?<!白)附子", "附片", "顺片"),
     ("半夏", "瓜蒌", "栝楼", "天花粉", "贝母", "川贝", "浙贝", "白蔹", "白及")),
    ("十八反", "藜芦反人参、沙参、丹参、玄参、苦参、细辛、芍药",
     ("藜芦",),
     ("人参", "党参", "西洋参", "沙参", "丹参", "玄参", "苦参", "细辛", "芍")),
    ("十九畏", "硫黄畏朴硝", ("硫黄",), ("朴硝", "芒硝", "玄明粉")),
    ("十九畏", "水银畏砒霜", ("水银",), ("砒霜", "砒石", "信石")),
    ("十九畏", "狼毒畏密陀僧", ("狼毒",), ("密陀僧",)),
    ("十九畏", "巴豆畏牵牛", ("巴豆",), ("牵牛", "黑丑", "白丑", "二丑")),
    ("十九畏", "丁香畏郁金", ("丁香",), ("郁金",)),
    ("十九畏", "川乌、草乌畏犀角", ("川乌", "草乌", "乌头"), ("犀角",)),
    ("十九畏", "牙硝畏三棱", ("牙硝", "芒硝", "朴硝"), ("三棱",)),
    ("十九畏", "官桂畏赤石脂", ("官桂", "肉桂"), ("赤石脂",)),
    ("十九畏", "人参畏五灵脂", ("人参",), ("五灵脂",)),
)

_COMPILED = tuple(
    (kind, desc, re.compile("|".join(a)), re.compile("|".join(b)))
    for kind, desc, a, b in RULES
)


def find_conflicts(herbs):
    """返回处方中触及配伍禁忌的条目列表。"""
    names = [h.strip() for h in herbs if h and h.strip()]
    conflicts = []
    for kind, desc, left, right in _COMPILED:
        hits_left = [n for n in names if left.search(n)]
        hits_right = [n for n in names if right.search(n)]
        if hits_left and hits_right:
            involved = list(dict.fromkeys(hits_left + hits_right))
            conflicts.append({"kind": kind, "desc": desc, "herbs": involved})
    return conflicts


def rules_for_js():
    return [
        {"kind": kind, "desc": desc, "a": "|".join(a), "b": "|".join(b)}
        for kind, desc, a, b in RULES
    ]
