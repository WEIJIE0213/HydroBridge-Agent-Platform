from app.importers.water_data import normalize_document, normalize_title


def test_normalize_water_achievement_document() -> None:
    document = {
        "entities": [
            {
                "type": "科技成果",
                "name": "农村供水智能监测技术",
                "attributes": {
                    "成果来源": "示范项目",
                    "所属领域": "农村饮水、智慧水利",
                    "完成单位": ["某水利科研院"],
                    "立项背景": "解决偏远供水工程巡检困难问题。",
                },
            },
            {
                "type": "待解决问题",
                "name": "漏损发现不及时",
                "attributes": {
                    "问题描述": "人工巡检周期长。",
                    "挑战因素": ["通信条件不稳定"],
                },
            },
            {
                "type": "成果的核心技术",
                "name": "边缘监测",
                "attributes": {"技术描述": "在泵站侧完成异常检测。"},
            },
            {
                "type": "性能指标",
                "name": "告警延迟",
                "attributes": {"指标名称": "告警延迟", "指标值、单位": "≤ 5 min"},
            },
            {
                "type": "应用实例",
                "name": "山区供水示范工程",
                "attributes": {"应用效果": "漏损发现时间缩短 70%"},
            },
        ]
    }

    result = normalize_document(document, "example.json")

    assert result["normalized_title"] == normalize_title("农村供水智能监测技术")
    assert result["taxonomy_code"] == "water-supply"
    assert result["organizations"] == ["某水利科研院"]
    assert result["maturity_level"] == 7
    assert result["metrics"][0]["value"] == "≤ 5 min"
    assert "通信条件不稳定" in result["limitations"]
    assert result["status"] == "approved"
