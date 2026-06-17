def build_scenarios(direction: str, setup: dict, targets: dict, intermarket: dict, invalidation_text: str) -> dict:
    setup_label = (setup or {}).get("primary_setup_label") or "方向確認待ち"
    confirmation = (intermarket or {}).get("confirmation_label")
    us_text = _us_confirmation_text(confirmation)

    if direction == "up":
        baseline = f"{setup_label}。押し目を作った後、直近高値の再試行を基本にします。"
        upside = "米国3指数の上昇確認と日経先物の高値終値突破が重なる場合、上値抵抗ゾーンを拡張します。"
        downside = "EMA20または前日安値を明確に割り込み、米国3指数も失速する場合は上昇失敗として下値支持ゾーンを確認します。"
    elif direction == "down":
        baseline = f"{setup_label}。戻りを作った後、下値支持ゾーンの再確認を基本にします。"
        upside = "米国3指数が上昇確認へ転じ、日経先物も戻り高値を超える場合はショート追撃を止めます。"
        downside = "米国3指数の下落確認と日経先物の安値終値割れが重なる場合、下値支持ゾーンを拡張します。"
    else:
        baseline = f"{setup_label}。上下どちらかの重要価格帯を終値で抜けるまで待ちます。"
        upside = "米国3指数が上昇確認となり、日経先物が高値を終値で抜ける場合だけ上振れを見ます。"
        downside = "米国3指数が下落確認となり、日経先物が安値を終値で割る場合だけ下振れを見ます。"

    return {
        "baseline": {
            "label": "基本シナリオ",
            "text": f"{baseline} {us_text}",
        },
        "upside": {
            "label": "上振れシナリオ",
            "text": upside,
            "targets": (targets or {}).get("upside") or [],
        },
        "downside": {
            "label": "下振れシナリオ",
            "text": downside,
            "targets": (targets or {}).get("downside") or [],
        },
        "invalidation": {
            "label": "無効化ライン",
            "text": invalidation_text or "日経先物の重要価格帯で判断します。",
            "level": (targets or {}).get("invalidation") or {},
        },
    }


def _us_confirmation_text(label):
    return {
        "confirm_up": "米国3指数確認は上向きで、信頼度を補助します。",
        "confirm_down": "米国3指数確認は下向きで、追いかけリスクに注意します。",
        "divergent": "米国3指数確認は分裂しており、追いかけは避けます。",
        "mixed": "米国3指数確認はまちまちです。",
    }.get(label, "米国3指数確認はデータ待ちです。")
