# Media Import Addon

URL を `.loft` ファイル（reference）として取り込み、メタデータ・字幕・provider 対応の埋め込みプレイヤーを提供する Litloft アドオン。

## 責務

- 単一 URL → `.loft` 生成 (provider/url JSON)
- yt-dlp によるメタデータ取得（タイトル・channel・description・published_at・thumbnail）
- 字幕 (.vtt) のダウンロードと dedup
- 公式 provider 実装の提供（YouTube / Vimeo / SoundCloud）
- `LoftMetadataPanel` を `loft-metadata` slot に注入
- `loft_metadata` テーブル所有

実ファイル DL（yt-dlp で動画を mp4 として保存）は **Downloader アドオン**の責務。

## アーキテクチャ

抽象 API（`playerRegistry` / `LoftPlayer` / `GenericLinkCard` / `provider_registry`）は Litloft Core が保証する contract。Media Import はその contract に対して公式 provider 実装を登録するアドオンに過ぎない。第三者 addon（例: `import-adult`）も同じ仕組みで `registerLoftPlayer("xvideos", XvideosEmbed)` できる。

## セットアップ

```bash
git clone https://github.com/mamepenguin/media_import.git addons/media_import
docker compose up -d --build
```

`/drive/{drive}/addons/media_import` でアクセスできる。

## API

| メソッド | パス | 説明 |
|---------|------|------|
| POST | /api/addons/media_import/link | URL → `.loft` 生成（drive, folder_path 指定） |
| GET | /api/addons/media_import/link/{file_id}/metadata | loft_metadata 取得 |
| POST | /api/addons/media_import/link/{file_id}/refresh | メタデータ・字幕の再取得をキュー |

## Policy

`drives.json` で per-drive に enable/disable できる:

```jsonc
{
  "addons": {
    "media_import": {
      "url_import": true
    }
  }
}
```

未指定キーは graceful degradation で enable される。
