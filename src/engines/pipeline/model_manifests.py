"""Pinned file lists of every downloadable Hugging Face-hosted model.

Sizes and SHA-256 digests come from the pinned Hugging Face revision; the ModelScope
repositories listed alongside were checked to serve byte-identical copies.
"""

from __future__ import annotations

from src.utils.model_fetch import ModelFile, RepoManifest

_F = ModelFile

WHISPER: dict[str, RepoManifest] = {
    "medium": RepoManifest(
        "Systran/faster-whisper-medium",
        "08e178d48790749d25932bbc082711ddcfdfbc4f",
        (
            _F(
                "config.json",
                2257,
                "3622a2ddc41ec0e0fd4e68c13c6830f03b90c38d89aaad184de02c8c642cf807",
            ),
            _F(
                "model.bin",
                1527906378,
                "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae",
            ),
            _F(
                "tokenizer.json",
                2203239,
                "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
            ),
            _F(
                "vocabulary.txt",
                459861,
                "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
            ),
        ),
        ("Systran/faster-whisper-medium", "pengzhendong/faster-whisper-medium"),
    ),
    "large-v3": RepoManifest(
        "Systran/faster-whisper-large-v3",
        "edaa852ec7e145841d8ffdb056a99866b5f0a478",
        (
            _F(
                "config.json",
                2394,
                "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9",
            ),
            _F(
                "model.bin",
                3087284237,
                "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1",
            ),
            _F(
                "preprocessor_config.json",
                340,
                "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
            ),
            _F(
                "tokenizer.json",
                2480617,
                "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca",
            ),
            _F(
                "vocabulary.json",
                1068114,
                "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
            ),
        ),
        ("Systran/faster-whisper-large-v3", "keepitsimple/faster-whisper-large-v3"),
    ),
}

_QWEN_COMMON = (
    _F(
        "chat_template.jinja",
        1434,
        "f50e6b694fbf4a683206e37869990d68333fe95d285730f084c838a34b0d98c2",
    ),
    _F(
        "generation_config.json",
        165,
        "9939fc9388b79bd70757f938b87381e817173d6a6158f5af6506c0b73e775c3c",
    ),
    _F(
        "processor_config.json",
        487,
        "bc0b230081b44e629dd5b9045b78495615c1831b4b9f4cffe97bd37e82a6156a",
    ),
    _F(
        "tokenizer.json",
        11429653,
        "fe1fad59be22a41ee293363fcf95fdedbc7c93f3b49270b1d2e18bd1399a7a05",
    ),
    _F(
        "tokenizer_config.json",
        998,
        "945e980986de2ca7768f3326bfdbb4fbea3406f972b8ae0be233089f2b253c11",
    ),
)

QWEN_ASR: dict[str, RepoManifest] = {
    "Qwen/Qwen3-ASR-0.6B-hf": RepoManifest(
        "Qwen/Qwen3-ASR-0.6B-hf",
        "7f1569a48a89f3e3f4dc3a5c9d28bddd903bc76c",
        (
            _F(
                "config.json",
                2398,
                "9eecf6f1b383e343889c2e6010e632590fa57d4bc678e151c7d6a160a0dfb04a",
            ),
            _F(
                "model.safetensors",
                1564928088,
                "d3f212dd20abecd315d830bc54ae3865e56ebfc3276484e57b771288ba27fd35",
            ),
            *_QWEN_COMMON,
        ),
        ("Qwen/Qwen3-ASR-0.6B-hf",),
    ),
    "Qwen/Qwen3-ASR-1.7B-hf": RepoManifest(
        "Qwen/Qwen3-ASR-1.7B-hf",
        "bcd2b5b7f32b480ab5790554cfa8347f246a14f3",
        (
            _F(
                "config.json",
                2399,
                "117ac8e63e2af7cae3665e5a632d6eb03f5f384915519ceb6403c15ec6533f63",
            ),
            _F(
                "model.safetensors",
                4076193080,
                "2db53c7d81bd9b8cbc6a074e89be2c968a0d373fb4ee68bb1b1e14f7042dfee1",
            ),
            *_QWEN_COMMON,
        ),
        ("Qwen/Qwen3-ASR-1.7B-hf",),
    ),
}

HYMT: dict[str, RepoManifest] = {
    "tencent/Hy-MT2-1.8B-GGUF": RepoManifest(
        "tencent/Hy-MT2-1.8B-GGUF",
        "a0c709d9fac510f2c807aa3af52872340dc37a4a",
        (
            _F(
                "Hy-MT2-1.8B-Q4_K_M.gguf",
                1133080448,
                "dc5f44fcf1fa496ee7ad725982c0c8c553a4de00259b53af84c4b89fb0c06699",
            ),
        ),
        ("Tencent-Hunyuan/Hy-MT2-1.8B-GGUF",),
    ),
    "tencent/Hy-MT2-7B-GGUF": RepoManifest(
        "tencent/Hy-MT2-7B-GGUF",
        "ab8472660ac61fac25f1af43fac2599d52a8a775",
        (
            _F(
                "Hy-MT2-7B-Q4_K_M.gguf",
                4624648896,
                "9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b",
            ),
        ),
        ("Tencent-Hunyuan/Hy-MT2-7B-GGUF",),
    ),
}

_NLLB_TOKENIZER = (
    _F(
        "sentencepiece.bpe.model",
        4852054,
        "14bb8dfb35c0ffdea7bc01e56cea38b9e3d5efcdcb9c251d6b40538e1aab555a",
    ),
    _F(
        "special_tokens_map.json",
        3548,
        "992bd4ed610d644d6823081937bcc91bb8878dd556cea4ae5327f2480361330e",
    ),
    _F(
        "tokenizer.json",
        17331176,
        "e316b82de11d0f951f370943b3c438311629547285129b0b81dadabd01bca665",
    ),
    _F(
        "tokenizer_config.json",
        564,
        "d1aa8c3697d3e35674f97b5b7e9c99d22b010f528e80140257d97316be90d044",
    ),
)

# No ModelScope copy of the CTranslate2 conversions exists; Hugging Face only.
NLLB: dict[str, RepoManifest] = {
    "JustFrederik/nllb-200-distilled-600M-ct2-int8": RepoManifest(
        "JustFrederik/nllb-200-distilled-600M-ct2-int8",
        "302d78f00e6fdb50a1064059df7c392b735e9d05",
        (
            _F(
                "config.json",
                159,
                "0c2f6fa2057c7264d052fb4a62ba3476eeae70487acddfa8e779a53a00cbf44c",
            ),
            _F(
                "model.bin",
                622595991,
                "ed1beaf75134de7505315a5223162f56acff397eff6b50638a500d3936fe707b",
            ),
            _F(
                "shared_vocabulary.txt",
                2568098,
                "a132a83330f45514c2476eb81d1d69b3c41762264d16ce0a7ea982e5d6c728e5",
            ),
            *_NLLB_TOKENIZER,
        ),
    ),
    "mijuanlo/nllb-200-distilled-1.3B-int8-ct2": RepoManifest(
        "mijuanlo/nllb-200-distilled-1.3B-int8-ct2",
        "6eee5eda03ff1441d2a6117d34a02e44504ce321",
        (
            _F(
                "config.json",
                1027,
                "61addea997996a86ed31d82ba7f1790552a01a915728a47b090cd2ccb159f498",
            ),
            _F(
                "generation_config.json",
                189,
                "0bb604cdb8392649176935a447ce994128f170a6908c135a17d0e3dfb8113cb2",
            ),
            _F(
                "model.bin",
                1381827201,
                "d02c1ceb6d79dcd3ac06583d4f84060a49d248939ad6f7b00d60853b09c18702",
            ),
            _F(
                "shared_vocabulary.json",
                5921176,
                "af53bfd0e6f726209e7325e45b87ab3b14e5856f7d42d7b9be91de3287c45267",
            ),
            *_NLLB_TOKENIZER,
        ),
    ),
}

SHERPA_ASR: dict[str, RepoManifest] = {
    "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20": RepoManifest(
        "csukuangfj/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20",
        "98590b7ed6443e77b714204da2757d75e1a642f4",
        (
            _F(
                "encoder-epoch-99-avg-1.int8.onnx",
                181895032,
                "8fa764187a261844f859d7143ebaa563af5d10adfece4c18a8f414c88cba2a9b",
            ),
            _F(
                "decoder-epoch-99-avg-1.int8.onnx",
                13091040,
                "1a70c593d71e53f023f5f55b0b4cfff5055abb786ee3992e5f63dc2e273cc4fa",
            ),
            _F(
                "joiner-epoch-99-avg-1.int8.onnx",
                3228404,
                "1ed689c5ed19dbaa725d9d191bb4822b5f4855a39e1ffd28cbc1f340d25b2ee0",
            ),
            _F(
                "tokens.txt",
                56317,
                "a8e0e4ec53810e433789b54a5c0134a7eaa2ffca595a6334d54c00da858841d3",
            ),
        ),
        # pkufool is a sherpa-onnx maintainer.
        ("pkufool/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20",),
    ),
    # No ModelScope copy carries the int8 files; Hugging Face only.
    "sherpa-onnx-streaming-zipformer-en-2023-06-26": RepoManifest(
        "csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26",
        "672fbf1b30579d6585301139bb363f42a0ad4a24",
        (
            _F(
                "encoder-epoch-99-avg-1-chunk-16-left-64.int8.onnx",
                71082637,
                "0d072fd4ef956294ba9db9e9a71a541ac70659095ec4934c8453d8b2fe740187",
            ),
            _F(
                "decoder-epoch-99-avg-1-chunk-16-left-64.int8.onnx",
                1307236,
                "98da299f471e38bb4e1a8df579b8cc9122d6039576a77e357b3c60f17dd83b02",
            ),
            _F(
                "joiner-epoch-99-avg-1-chunk-16-left-64.int8.onnx",
                259335,
                "d944208d660d67c8d72cd2acaeac971fa5ceb8c80e76c1968148846fedd6e297",
            ),
            _F(
                "tokens.txt",
                5048,
                "49e3c2646595fd907228b3c6787069658f67b17377c60aeb8619c4551b2316fb",
            ),
        ),
    ),
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17": RepoManifest(
        "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
        "2365baeacb507f821a0c8120fcee3d484dba7a07",
        (
            _F(
                "model.int8.onnx",
                239233841,
                "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51",
            ),
            _F(
                "tokens.txt",
                315894,
                "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc",
            ),
        ),
        (
            "fengge2024/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
            "mariolux/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
            "WEAAEW/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
        ),
    ),
}
