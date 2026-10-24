## HTTP calls: 56 (17 GET, 39 POST) to 31 API endpoints

## By endpoint
- `(non-API url)`  GET  sites=8  body=-  kwargs=allow_redirects,headers,params,stream
    0.2.18.1.py:996 _karafun_rows  timeout=4
    0.2.18.1.py:1248 _fetch_latest_release  timeout=12
    0.2.18.1.py:1260 _fetch_release_manifest  timeout=12
    0.2.18.1.py:1326 _download_asset  timeout=(12, 30)
    0.2.18.1.py:41417 _net_fetch_accepting  timeout=5
    0.2.18.1.py:41486 _net_fetch_waitlist_enabled  timeout=5
    0.2.18.1.py:48739 search.worker  timeout=10
    0.2.18.1.py:48865 _room_fetch_gif.worker  timeout=15
- `api.php`  POST  sites=1  body=json  kwargs=headers,json,params
    0.2.18.1.py:61297 _clear_remote_queue_async.send  timeout=5
- `chat_media.php`  GET/POST  sites=2  body=data  kwargs=data,files,headers,params
    0.2.18.1.py:48686 _room_say_photo.worker  timeout=40
    0.2.18.1.py:48841 _room_fetch_media.worker  timeout=15
- `clear_remote_queue.php`  POST  sites=1  body=data  kwargs=data,headers
    0.2.18.1.py:61265 _clear_remote_queue_async.send  timeout=8
- `complete_remote_request.php`  POST  sites=2  body=data  kwargs=data,headers
    0.2.18.1.py:60615 _sync_remote_removal_tombstones_async.send  timeout=6
    0.2.18.1.py:60770 _delete_remote_request.send  timeout=5
- `delete_remote_request.php`  POST  sites=3  body=data  kwargs=data,headers
    0.2.18.1.py:36745 _cleanup_terminal_removed_requests.worker  timeout=5
    0.2.18.1.py:60800 _delete_remote_request.send  timeout=5
    0.2.18.1.py:61283 _clear_remote_queue_async.send  timeout=5
- `get_phrase_markers.php`  GET  sites=1  body=-  kwargs=headers,params
    0.2.18.1.py:46574 _pull_phrase_markers.fetch  timeout=8
- `get_requests.php`  GET  sites=1  body=-  kwargs=headers,params
    0.2.18.1.py:12656 run  timeout=8
- `get_requests_v2.php`  GET/POST  sites=2  body=json  kwargs=headers,json,params
    0.2.18.1.py:28910 fetch_remote_requests_once.worker  timeout=10
    0.2.18.1.py:29162 ack_remote_requests.worker  timeout=10
- `host_chat.php`  GET/POST  sites=3  body=data  kwargs=data,headers,params
    0.2.18.1.py:49034 _schedule_chat_poll.worker  timeout=6
    0.2.18.1.py:49062 _clear_chat_history.worker  timeout=8
    0.2.18.1.py:49090 _chat_mark_current_read.mark_read  timeout=6
- `host_chat_moderation.php`  GET/POST  sites=6  body=data  kwargs=data,headers,params
    0.2.18.1.py:48337 _schedule_room_poll.worker  timeout=8
    0.2.18.1.py:48652 _room_say_send.worker  timeout=10
    0.2.18.1.py:48730 search.worker  timeout=12
    0.2.18.1.py:48781 _room_action.worker  timeout=8
    0.2.18.1.py:48810 _room_clear_night.worker  timeout=10
    0.2.18.1.py:48971 _host_chat_send_media.worker  timeout=10
- `host_commands.php`  GET/POST  sites=3  body=data  kwargs=data,headers,params
    0.2.18.1.py:12722 run  timeout=3
    0.2.18.1.py:34064 _sync_host_control_state_now.send  timeout=3
    0.2.18.1.py:34211 _ack_host_control_command.send  timeout=3
- `mark_remote_request_delivered.php`  POST  sites=1  body=data  kwargs=data,headers
    0.2.18.1.py:36543 _mark_waiting_for_add_delivered_async.worker  timeout=5
- `post_rotation.php`  POST  sites=3  body=data  kwargs=data,headers,params
    0.2.18.1.py:61312 _clear_remote_queue_async.send  timeout=5
    0.2.18.1.py:62787 post_rotation.send  timeout=5
    0.2.18.1.py:62802 post_rotation.send  timeout=5
- `rename_request_singer.php`  POST  sites=1  body=data  kwargs=data,headers
    0.2.18.1.py:47170 _push_singer_rename_to_server.send  timeout=8
- `replace_remote_request.php`  POST  sites=1  body=data  kwargs=data,headers
    0.2.18.1.py:49719 _push_remote_request_replacement.send  timeout=6
- `report_pending_request.php`  POST  sites=1  body=data  kwargs=data,headers
    0.2.18.1.py:61175 _report_remote_attention_request_async.worker  timeout=4
- `restore_remote_request.php`  POST  sites=1  body=data  kwargs=data,headers
    0.2.18.1.py:34988 _restore_waitlist_request_on_server_async.worker  timeout=5
- `send_direct_message.php`  POST  sites=1  body=data  kwargs=data,headers
    0.2.18.1.py:49216 _net_send_direct_message  timeout=8
- `send_stage_notification.php`  POST  sites=1  body=data  kwargs=data,headers
    0.2.18.1.py:49267 _net_send_stage_notification  timeout=8
- `set_accepting.php`  POST  sites=1  body=data  kwargs=data
    0.2.18.1.py:41450 _net_set_accepting  timeout=10
- `set_host_controls.php`  POST  sites=1  body=data  kwargs=data,headers
    0.2.18.1.py:41544 _net_set_host_controls  timeout=5
- `set_phrase_markers.php`  POST  sites=1  body=data  kwargs=data,headers
    0.2.18.1.py:46543 _sync_push_phrase_markers.send  timeout=8
- `set_remote_request_modifiers.php`  POST  sites=1  body=data  kwargs=data,headers
    0.2.18.1.py:60860 _push_remote_request_modifiers.send  timeout=5
- `set_remote_request_order.php`  POST  sites=1  body=data  kwargs=data,headers
    0.2.18.1.py:61369 _sync_remote_singer_order.send  timeout=5
- `set_session_location.php`  POST  sites=1  body=data  kwargs=data,headers
    0.2.18.1.py:41394 sync_session_location.send  timeout=8
- `set_waiting_for_add.php`  POST  sites=1  body=data  kwargs=data,headers
    0.2.18.1.py:41509 _net_set_waitlist_enabled  timeout=5
- `singer_history_sync.php`  POST  sites=1  body=json  kwargs=headers,json
    0.2.18.1.py:48032 _sync_singer_history_async.send  timeout=12
- `snapshot.php`  GET/POST  sites=2  body=data  kwargs=data,files,headers,params
    0.2.18.1.py:33504 _schedule_daw_singer_screen_snapshot.check_viewer  timeout=2
    0.2.18.1.py:33784 _post_daw_singer_screen_snapshot.encode_and_send  timeout=3
- `support_logs.php`  POST  sites=1  body=data  kwargs=data,files,headers
    0.2.18.1.py:2681 send_log_package_to_developer  timeout=30
- `upload_songbook.php`  POST  sites=1  body=data  kwargs=data,files,headers
    0.2.18.1.py:1469 run  timeout=180
- `upsert_host_request.php`  POST  sites=1  body=data  kwargs=data,headers,params
    0.2.18.1.py:44300 _flush_host_request_sync_ops.send  timeout=7

## Risk facts for a port
- calls with NO timeout: 0
- calls that disable TLS verification: 0
- endpoints called from several places: 9: chat_media.php x2, complete_remote_request.php x2, delete_remote_request.php x3, get_requests_v2.php x2, host_chat.php x3, host_chat_moderation.php x6, host_commands.php x3, post_rotation.php x3, snapshot.php x2
- calls whose URL is not a *.php API path: 8
    0.2.18.1.py:996 _karafun_rows GET self.karafun_search_url
    0.2.18.1.py:1248 _fetch_latest_release GET url
    0.2.18.1.py:1260 _fetch_release_manifest GET url
    0.2.18.1.py:1326 _download_asset GET url
    0.2.18.1.py:41417 _net_fetch_accepting GET url
    0.2.18.1.py:41486 _net_fetch_waitlist_enabled GET url
    0.2.18.1.py:48739 search.worker GET url
    0.2.18.1.py:48865 _room_fetch_gif.worker GET url
