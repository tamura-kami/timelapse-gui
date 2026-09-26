# タイムラプス撮影 GUI

Ubuntu のデスクトップで `fswebcam` を使って定間隔撮影する GTK4 アプリです。撮影画像は `00001.jpg` のような5桁以上の連番で保存されます。同じ保存先を再利用する場合は、既存の連番の続きから保存します。画面に撮影状態・枚数・最新画像を表示します。

## 必要パッケージ

```sh
sudo apt update
sudo apt install fswebcam v4l-utils python3-gi gir1.2-gtk-4.0 python3-venv \
  python3-gst-1.0 gir1.2-gstreamer-1.0 \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good
```

プロジェクト直下に仮想環境を作成します。GTK4 と PyGObject は Ubuntu の APT パッケージを使うため、`--system-site-packages` を指定します。すでに `venv/` がある場合は作成をスキップできます。

```sh
python3 -m venv --system-site-packages ./venv
```

## カメラの確認

アプリを起動する前に、ターミナルから `fswebcam` がカメラを使えることを確認してください。

```sh
mkdir -p ~/Pictures/timelapse
fswebcam --no-banner --resolution 1280x720 ~/Pictures/timelapse/camera-check.jpg
```

コマンドが成功し、`camera-check.jpg` が作成されれば利用できます。デバイス権限やカメラの使用中などの理由で失敗した場合は、`fswebcam` のエラーを確認してください。

## 起動

プロジェクトディレクトリで次を実行します。

```sh
./venv/bin/python main.py
```

保存先の初期値は `~/Pictures/timelapse/YYYYMMDD-HHMM`（アプリ起動時刻）です。「新規」を押すと `~/Pictures/timelapse` の下に現在時刻名のディレクトリを作成し、保存先を切り替えます。同じ時刻名がすでにある場合は連番を付けます。起動中および撮影間隔の待機中は「カメラプレビュー / 最新画像」にライブ映像を16:9で表示します。撮影時はプレビューを一時停止してカメラを `fswebcam` に渡し、撮影後にプレビューを再開します。カメラデバイスは `/dev/video0` を使用します。

撮影間隔（0.1秒刻み、最小0.1秒）を設定して「撮影開始」を押すと、直ちに1枚撮影し、その後は指定した間隔で撮影します。撮影間隔は `~/.config/timelapse-gui/settings.json` に保存され、次回起動時に復元されます。撮影中は設定を変更できません。「撮影停止」で次回以降の撮影を停止します。

カメラが対応している場合は「明るさ」「コントラスト」「彩度」のスライダーで調整できます。利用可能な項目だけが操作でき、値は設定ファイルに保存されて次回起動時に復元されます。カメラが対応していない項目や `/dev/video0` を開けない場合は、その項目に「未対応または未接続」と表示されます。

撮影停止後に「動画化」を押すと、保存先とファイル名を選ぶダイアログが開きます。初期ファイル名は `保存先名_stabilized.mp4` です。選んだ場所に、現在の保存先にある画像から手ブレ補正付きのMP4動画を10 fpsで作成します。「BGMを選択…」で音声ファイルを指定すると、動画の長さに合わせてBGMをループ再生します。選択したBGMと音量（0〜200%、初期値50%）は `~/.config/timelapse-gui/settings.json` に保存され、次回起動時に復元されます。「BGMなし」を選ぶとBGMの選択が解除され、無音の動画になります。動画化には `ffmpeg` と `vidstabdetect` / `vidstabtransform` フィルターが必要です。
