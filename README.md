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
| GET | /api/addons/media_import/watch | Watch 面の 1 レーン（`lane=continue\|regular\|feed`） |

## Watch 面

ページは **Watch**（見る）と **Manage**（管理）の 2 ビューに分かれる。

取り込みは視聴意思を意味しない。動画を取り込む主目的は字幕・メタデータ検索と Ask であり、未再生の動画を未処理 Inbox として積み上げない。そのため購読ごとに表示レベルを持つ:

| モード | 意味 | Watch での扱い |
|---|---|---|
| `library`（既定） | 取り込んで検索対象にする | レーンに出さない。再生途中なら「再生途中」には出る |
| `feed` | 新しいうちは気になるかもしれない | 新着レーンに時系列で並ぶ |
| `regular` | 習慣的に見るソース | よく見るソースレーンに優先表示 |

- 既存購読と新規購読はどちらも `library` から始まる。昇格は利用者の明示操作のみ。
- モード変更は表示のみに影響する。再取り込み・再インデックス・ファイル移動は一切起きない。
- 未読件数やバックログ総数は出さない。`/watch` は総件数を返さない。
- 再生状態は Core の `WatchHistory` が正典。Media Import は独自の視聴済みフラグを持たない。
- 「後で見る」は Core Collection に追加する。独自の Watch Later は作らない。

設計: `docs/superpowers/specs/2026-08-10-media-import-watch-surface.md`

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
