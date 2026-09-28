"""问诊记录字段定义。

表单、详情页、打印页和 CSV 导出都从这里读取字段。
新增一个文字字段时，在这里和 schema.sql 的 visits 表中各加一处即可。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    multiline: bool = False
    # 独占一整行（多行文本总是独占一行）
    wide: bool = False
    # 常用描述，点击即可填入（再次点击取消）
    options: tuple = ()
    # 多个选项之间的连接符；脉象习惯连写（如“弦细数”），故为空
    joiner: str = "，"
    placeholder: str = ""
    # 男性患者默认折叠
    female_only: bool = False


VISIT_SECTIONS = (
    ("主诉与病史", (
        Field("chief_complaint", "主诉", wide=True,
              placeholder="主要症状及持续时间，如：胃脘胀痛反复发作3月"),
        Field("present_illness", "现病史", multiline=True,
              placeholder="起病经过、诊治经过、目前症状"),
    )),
    ("问诊（十问）", (
        Field("cold_heat", "寒热", options=(
            "无明显寒热", "恶寒", "畏寒", "发热", "恶寒发热", "寒热往来",
            "潮热", "五心烦热", "手足不温")),
        Field("sweating", "汗", options=(
            "汗出正常", "无汗", "自汗", "盗汗", "汗多", "头汗", "手足心汗")),
        Field("head_body", "头身", options=(
            "头痛", "头晕", "头重如裹", "身重", "身痛", "乏力", "腰膝酸软",
            "肢体麻木", "关节疼痛")),
        Field("chest_abdomen", "胸腹", options=(
            "胸闷", "心悸", "胁肋胀痛", "胃脘胀满", "胃痛", "腹胀", "腹痛",
            "嗳气", "反酸")),
        Field("diet", "饮食口味", options=(
            "纳可", "纳差", "食少", "多食易饥", "口苦", "口淡", "口甜", "口黏腻")),
        Field("thirst", "口渴饮水", options=(
            "口不渴", "口干", "口渴喜冷饮", "口渴喜热饮", "渴不欲饮")),
        Field("sleep", "睡眠", options=(
            "眠可", "入睡困难", "多梦", "易醒", "早醒", "嗜睡")),
        Field("stool", "大便", options=(
            "大便调", "便溏", "便秘", "大便干结", "大便黏滞", "先干后溏", "完谷不化")),
        Field("urine", "小便", options=(
            "小便调", "小便黄", "小便清长", "尿频", "尿急", "夜尿多", "小便短赤")),
        Field("ears_eyes", "耳目", options=(
            "耳鸣", "耳聋", "目干涩", "视物模糊", "目赤")),
        Field("menstruation", "经带胎产", female_only=True,
              placeholder="末次月经、周期、经量、带下、孕产史",
              options=("月经规律", "月经先期", "月经后期", "经期延长", "经量多",
                       "经量少", "痛经", "有血块", "带下量多", "已绝经")),
    )),
    ("望闻切", (
        Field("inspection", "望诊", placeholder="神、色、形、态", options=(
            "神清", "精神可", "神疲", "面色红润", "面色萎黄", "面色苍白",
            "面色晦暗", "面红", "形体偏胖", "形体消瘦")),
        Field("tongue_body", "舌质", options=(
            "淡红", "淡白", "红", "绛", "暗", "紫暗", "胖大", "齿痕", "瘦薄",
            "裂纹", "瘀点")),
        Field("tongue_coating", "舌苔", options=(
            "薄白", "白腻", "白厚", "薄黄", "黄腻", "黄厚", "少苔", "无苔",
            "剥苔", "润", "燥")),
        Field("pulse", "脉象", joiner="", options=(
            "浮", "沉", "迟", "数", "滑", "涩", "弦", "细", "弱", "缓", "紧",
            "濡", "洪", "虚", "实", "结", "代")),
        Field("listening", "闻诊", options=(
            "语声有力", "语声低微", "咳嗽", "气短", "口气臭秽")),
        Field("physical_exam", "体格检查", placeholder="体温、血压、心率等"),
        Field("lab_results", "辅助检查", multiline=True,
              placeholder="化验、影像等检查结果"),
    )),
    ("诊断与治法", (
        Field("tcm_disease", "中医诊断", placeholder="病名，如：胃脘痛"),
        Field("syndrome", "证型", placeholder="如：肝胃不和证"),
        Field("western_diagnosis", "西医诊断"),
        Field("treatment_principle", "治则治法", placeholder="如：疏肝和胃，理气止痛"),
    )),
    ("其他治疗与医嘱", (
        Field("other_treatment", "其他治疗", multiline=True,
              placeholder="针灸、推拿、拔罐、艾灸、中成药等"),
        Field("advice", "医嘱", multiline=True, placeholder="饮食宜忌、起居调护等"),
    )),
)

VISIT_TEXT_FIELDS = tuple(f.key for _, fields in VISIT_SECTIONS for f in fields)
FIELD_LABELS = {f.key: f.label for _, fields in VISIT_SECTIONS for f in fields}

VISIT_TYPES = ("初诊", "复诊")

# 复诊时从上次就诊带入的字段。舌、脉、十问等需每次重新诊察，故不带入。
COPY_FIELDS = (
    "chief_complaint", "tcm_disease", "syndrome", "western_diagnosis",
    "treatment_principle", "formula_name", "dose_count", "usage",
    "other_treatment", "advice",
)
