"""Reviewed advertising entry-point patch specifications for ydsj.apk.

These are declarative review inputs, not an APK builder. Resolve every member by
its full DEX descriptor and validate the original method before applying. The
observed offsets and indices refer only to static-recovered/embedded-08.dex.
Keep original code-item lengths and the shell/native declarations unchanged.
"""

AD_ROOT = "Lcom/zjwh/android_wh_physicalfitness/advertise/"

ENTRY_PATCH_SPECS = [
    {
        "class": AD_ROOT + "AdManager;",
        "method": "isSlotAllowed",
        "descriptor": "(Ljava/util/List;II)Z",
        "replacement_kind": "return_false",
        "smali": ["const/4 v0, 0x0", "return v0"],
        "observed": {
            "dex": "embedded-08.dex", "method_idx": 4246,
            "code_off": 0x191A28, "registers_size": 9, "ins_size": 4,
            "outs_size": 4, "tries_size": 0, "insns_size": 73,
        },
        "reason": (
            "Route existing SDK slots through their existing blocking policy. "
            "AdManager.loadSplash calls onAdOff; both cold SplashActivity and "
            "hot AdActivity callbacks call startJump. TianMu clears/releases "
            "its container; AdGain reports slot_not_allowed and clears only "
            "its SDK slide through the normal failure result."
        ),
    },
    {
        "class": AD_ROOT + "AdManager;",
        "method": "checkCanLoad",
        "descriptor": "(" + AD_ROOT + "AdPosition;Z)Z",
        "replacement_kind": "return_false",
        "smali": ["const/4 v0, 0x0", "return v0"],
        "observed": {
            "dex": "embedded-08.dex", "method_idx": 4241,
            "code_off": 0x191784, "registers_size": 14, "ins_size": 3,
            "outs_size": 6, "tries_size": 0, "insns_size": 300,
        },
        "reason": (
            "Deny ad scheduling queries from lifecycle callers. The actual "
            "loadSplash method is retained, including its onAdOff and "
            "onAdTimeNot continuation callbacks. This is not a replacement "
            "for completing those callbacks."
        ),
    },
    {
        "class": AD_ROOT + "guandian/GuandianAdProvider;",
        "method": "loadVideoAd",
        "descriptor": "(Landroid/app/Activity;Ljava/lang/String;Lkotlin/jvm/functions/Function2;)V",
        "replacement_kind": "reward_unavailable_callback",
        "smali": [
            "const/4 v0, 0x0",
            "sput-boolean v0, " + AD_ROOT + "guandian/GuandianAdProvider;->easyRewardVideoAdLoading:Z",
            "move-object/from16 v0, p3",
            "sget-object v1, Ljava/lang/Boolean;->FALSE:Ljava/lang/Boolean;",
            "invoke-interface {v0, v1, v1}, Lkotlin/jvm/functions/Function2;->invoke(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
            "return-void",
        ],
        "observed": {
            "dex": "embedded-08.dex", "method_idx": 4591,
            "code_off": 0x1982D4, "registers_size": 27, "ins_size": 4,
            "callback_register": 26, "outs_size": 12,
            "tries_size": 0, "insns_size": 121,
            "replacement_insns_size": 11,
        },
        "references": {
            "loading_field": (AD_ROOT + "guandian/GuandianAdProvider;", "easyRewardVideoAdLoading", "Z"),
            "false_field": ("Ljava/lang/Boolean;", "FALSE", "Ljava/lang/Boolean;"),
            "callback_method": ("Lkotlin/jvm/functions/Function2;", "invoke", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;"),
        },
        "observed_reference_indices": {
            "loading_field": 23948, "false_field": 64793,
            "callback_method": 42910,
        },
        "reason": (
            "Reward video bypasses isSlotAllowed. Its original onAdFailed "
            "callback invokes adSuccess(Boolean.FALSE, Boolean.FALSE), then "
            "clears easyRewardVideoAdLoading. Return the same failure tuple "
            "and clear that flag before calling back. Never report exposure, "
            "completion, or a server reward. No SDK object is created."
        ),
    },
]

REVIEW_NOTES = {
    "covered_by_slot_policy": [
        "AdManager.loadSplash / loadInteraction / loadRunHistoryAd",
        "GuandianAdProvider.getAd: slot 4",
        "TianMuAdProvider.getSuspendAd: slots 9 and 7",
        "AdGainAdProvider.resolveShieldReason: slot 10",
    ],
    "do_not_noop": [
        "AdManager.loadSplash: its callback is needed to leave the splash page",
        "AdGainAdProvider.loadForPage: failure result removes the SDK slide",
        "AdvertiseControl.requestAdConfig: also fetches platform decorations",
    ],
    "unchanged_providers": [
        "XyzAdProvider.init is already empty; getAd only checks arguments",
        "LingYeAdProvider has no loading method",
    ],
    "coverage_limit": (
        "No additional unguarded SDK constructor was found in the recovered "
        "com.zjwh business Java outside Guandian reward video. Protected native "
        "method implementations are not visible, so their call graph cannot "
        "be certified by this static review. Reward-caller UI behavior and "
        "complete startup functionality still require device verification."
    ),
}
