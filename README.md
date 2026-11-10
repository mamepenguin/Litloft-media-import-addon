# Media Import Addon

URL を `.loft` ファイル（reference）として取り込み、メタデータ・字幕・provider 対応の埋め込みプレイヤーを提供する Litloft アドオン。

## 責務

- 単一 URL → `.loft` 生成 (provider/url JSON)
- yt-dlp によるメタデータ取得（タイトル・channel・description・published_at・thumbnail）
- 字幕 (.vtt) のダウンロードと dedup
- 公式 provider 実装の提供（YouTube / Vimeo / SoundCloud）
- `LoftMetadataPanel` を `loft-metadata` slot に注入
- Add メニュー（`folder-actions-menu`）に「URL から取り込む」行を注入。開いた場所のフォルダを保存先の初期値にして動画を 1 件取り込む。チャンネル・プレイリスト・フィードの購読はページの Manage で行う
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
| GET | /api/addons/media_import/watch | 1 レーンを 1 ページずつ（`lane=regular\|feed`、`after` で続きから） |

`/link` 系はすべて `X-Lit-Drive` ヘッダー（percent-encode したドライブ名）が必須。無ければ 400、ロック中のドライブや別ドライブのファイルを指すと 404 を返す。

## 動画アプリの発見

購読の `display_mode` は、動画アプリの発見に並ぶ 2 つの棚を決める。

| モード | 意味 | 動画アプリでの扱い |
|---|---|---|
| `library`（既定） | 取り込んで検索対象にする | 棚に出さない |
| `feed` | 新しいうちは気になるかもしれない | 「購読の新着」に新しい順で並ぶ |
| `regular` | 習慣的に見るソース | 「よく見るソース」に並ぶ |

- 既存購読と新規購読はどちらも `library` から始まる。取り込みは視聴意思を意味しない。
- 棚はページプロバイダ（`frontend/slots.ts` の `slotPageProviders`）として公開する。各ページは `/watch` を keyset カーソル（`after`）で 60 件ずつ読み、`restrict` が許可した行だけを棚の順で返す。1 回の呼び出しは最大 10 回読む。
- カードの 2 行目はチャンネル名と公開日（`チャンネル名 · 2026年11月1日`）。どちらかが無ければ片方だけ、両方無ければ出さない。
- 棚の「すべて見る」は動画アプリ内の棚ページを開く。
- モード変更は表示のみに影響する。再取り込み・再インデックス・ファイル移動は一切起きない。
- 総件数は出さない。`/watch` は総件数を返さない。

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

`url_import: false` のドライブでは Add メニューの「URL から取り込む」行が出ない。

未指定キーは graceful degradation で enable される。
