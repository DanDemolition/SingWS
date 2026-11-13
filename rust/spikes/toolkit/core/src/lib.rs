//! Toolkit-neutral model for the spike: reads the same files the Qt shell reads and uses the SAME `singws-uimodel` rules. Both prototypes call only this.
use serde_json::Value;
use singws_uimodel::{status, ticker, waitlist};

#[derive(Clone, Default, Debug, PartialEq)]
pub struct Row { pub id: i64, pub status: String, pub kind: String, pub text: String }
#[derive(Clone, Default, Debug, PartialEq)]
pub struct Section { pub title: String, pub rows: Vec<Row> }
#[derive(Clone, Default, Debug, PartialEq)]
pub struct Hero { pub kicker: String, pub singer: String, pub subtitle: String, pub remaining: String, pub format: String }
#[derive(Clone, Default, Debug, PartialEq)]
pub struct Snapshot { pub ticker_text: String, pub waitlist: Vec<Section>, pub waitlist_status: String, pub hero: Option<Hero>, pub status_line: String }

pub fn home() -> String {
    std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default()
}
fn read(path: String) -> Value { std::fs::read_to_string(path).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or(Value::Null) }

/// `custom_on` is the interactive control: it overrides `ticker_custom_enabled`, exactly what a Settings toggle would do.
pub fn load(dir: &str, custom_on: bool) -> Snapshot {
    let queue = read(format!("{dir}/queue.json"));
    let mut settings = read(format!("{dir}/settings.json"));
    if !settings.is_object() { settings = Value::Object(Default::default()); }
    settings["ticker_custom_enabled"] = Value::Bool(custom_on);
    let ticker_text = ticker::view(&queue, &settings, &Value::Null).map(|v| v.text).unwrap_or_else(|e| format!("ticker unavailable: {e}"));
    let snap = read(format!("{dir}/waitlist_snapshot.json"));
    let (waitlist, waitlist_status) = match waitlist::sections_from_snapshot(&snap) {
        Ok(s) => {
            let n: usize = s.iter().map(|x| x.rows.len()).sum();
            (s.iter().map(|x| Section { title: x.title.into(), rows: x.rows.iter().map(|r| Row { id: r.request_id, status: r.status_label.clone(), kind: r.status_kind.clone(), text: r.text.clone() }).collect() }).collect(), format!("{n} requests"))
        }
        Err(e) => (vec![], format!("waitlist unavailable: {e}")),
    };
    let (hero, status_line) = match status::view(&read(format!("{dir}/status_snapshot.json"))) {
        Ok(v) => (v.hero.map(|h| Hero { kicker: h.kicker, singer: h.singer, subtitle: h.subtitle, remaining: h.remaining, format: h.format_text }), v.summary),
        Err(e) => (None, format!("status unavailable: {e}")),
    };
    Snapshot { ticker_text, waitlist, waitlist_status, hero, status_line }
}

#[derive(Clone, Default, Debug, PartialEq)]
pub struct SongLine { pub id: i64, pub artist: String, pub title: String, pub meta: String }

/// Library search exactly as the Qt shell does it: strict search, then the typo-tolerant fallback. Returns (rows, elapsed ms, fuzzy used).
pub fn library_search(db: &str, query: &str, limit: i64) -> (Vec<SongLine>, u128, bool) {
    let t = std::time::Instant::now();
    match singws_uimodel::search_with_fuzzy(std::path::Path::new(db), query, limit, Some(std::time::Duration::from_millis(400))) {
        Ok(r) => (r.rows.iter().map(|s| SongLine { id: s.id, artist: s.artist.clone(), title: s.title.clone(), meta: format!("{}  {}", s.discid, singws_uimodel::format_duration(s.duration_secs)) }).collect(), t.elapsed().as_millis(), r.fuzzy_used),
        Err(_) => (vec![], t.elapsed().as_millis(), false),
    }
}

/// Synthetic library for scale tests (same columns the app's index has that the search reads). NOT real data.
pub fn generate_library(path: &str, n: usize) {
    let _ = std::fs::remove_file(path);
    let con = rusqlite::Connection::open(path).unwrap();
    con.execute_batch("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
        CREATE TABLE songs (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, artist TEXT, title TEXT, discid TEXT, duration_secs INTEGER, song_type TEXT, display TEXT, provider TEXT DEFAULT 'local', searchstring TEXT NOT NULL); BEGIN;").unwrap();
    let a = ["Queen", "ABBA", "Journey", "Adele", "Prince", "Madonna", "Oasis", "Blur", "Bon Jovi", "Whitney Houston", "Cyndi Lauper", "Elton John", "The Beatles", "Fleetwood Mac", "Taylor Swift", "Bruno Mars"];
    let w = ["love", "night", "dance", "heart", "fire", "rain", "dream", "road", "summer", "angel", "radio", "valerie", "waterloo", "thunder", "paradise", "forever"];
    for i in 0..n {
        let artist = format!("{} {}", a[i % a.len()], i / 16 % 40);
        let title = format!("{} {} {}", w[i * 7 % w.len()], w[(i / 3) % w.len()], i);
        let ss = format!("{artist} {title}").to_lowercase();
        con.execute("INSERT INTO songs (path, artist, title, discid, duration_secs, song_type, display, searchstring) VALUES (?,?,?,?,?,?,?,?)",
            rusqlite::params![format!("/lib/{i}.zip"), artist, title, format!("SC{:05}", i % 99999), 120 + (i % 240) as i64, "cdg", "", ss]).unwrap();
    }
    con.execute_batch("COMMIT;").unwrap();
}
