# Project World 自建邮件 DNS 配置

服务器已经部署本机提交模式的 Postfix 和 OpenDKIM。IDEA 通过 `127.0.0.1:25` 投递认证邮件；SMTP 不对公网开放，不能作为第三方中继使用。

在 `shiroha-rin.world` 的 DNS 控制台添加以下记录。

| 类型 | 主机记录 | 值 |
|---|---|---|
| A | `smtp` | `113.249.105.33` |
| TXT | `@` | `v=spf1 ip4:113.249.105.33 -all` |
| TXT | `_dmarc` | `v=DMARC1; p=none; adkim=s; aspf=s; pct=100` |
| TXT | `mail2026._domainkey` | 见下方完整 DKIM 公钥 |

DKIM TXT 值：

```text
v=DKIM1; h=sha256; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAyKbqwKAAoV5wR2acBr+tq3seBEFu9vPNZmxsLGelDNeqcOC+Ly7fraWpK2t0joCp5LIianjCvgQOn5yXkTT6qLzpi1vVTOdU+PmGx8Z2gJJrcFX9R7zCOCVL4+ljAI7VHY6x6g98iy+m07+DOEq4TX78YKYPWnyKKWHu+0/Jwh9Mp63YsTqwlOCiqt7j7EIMF0W26YXIaBFohBiqDBuaTU3qz+o/zb5C8A6K5hD2abeCTWW7xYoWYU90zLgUMMGEb7VesW7ySRQlGkPQsNQzZ6M/iSKV/w0E9d5liO0Ccu7PIoIjzIb6if3QnFraJEMIoC4DMNketO3qLFnmanbp3QIDAQAB
```

还需要在云服务器的 IP 反向解析控制台设置 PTR：

```text
113.249.105.33 -> smtp.shiroha-rin.world
```

DNS 生效后，检查 `smtp.shiroha-rin.world` 正向解析和 PTR 反向解析是否互相对应。首次运行时 DMARC 保持 `p=none`；经过实际投递验证后，再考虑收紧为 `quarantine` 或 `reject`。
