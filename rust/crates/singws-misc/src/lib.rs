//! Small pure rules of the app, each proven against the app's own Python by a tools/*_parity.py script.
use serde_json::Value;

pub type R<T> = Result<T, String>;

/// Python `float(v)` for a JSON value: numbers and booleans convert; null, lists and mappings raise (None); text is declined.
fn to_float(v: &Value) -> R<Option<f64>> {
    match v {
        Value::Number(n) => Ok(n.as_f64()),
        Value::Bool(b) => Ok(Some(*b as i64 as f64)),
        Value::String(_) => Err("decline: a number given as text".into()),
        _ => Ok(None),
    }
}

/// Python `int(v)` for the error code: numbers truncate, booleans are 0/1, null/lists/mappings raise (None), text is declined.
fn to_int(v: &Value) -> R<Option<i64>> {
    match v {
        Value::Bool(b) => Ok(Some(*b as i64)),
        Value::Number(n) => Ok(n.as_i64().or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f.trunc() as i64))),
        Value::String(_) => Err("decline: a number given as text".into()),
        _ => Ok(None),
    }
}

/// `LocationFixCollector` (0.2.18.1.py): decides when a CoreLocation search is finished.
#[derive(Default)]
pub struct LocationFixCollector {
    pub best: Option<Value>,
    pub best_accuracy: Option<f64>,
    pub transient_error: String,
    pub fatal_error: String,
}

impl LocationFixCollector {
    pub const GOOD_ACCURACY_M: f64 = 100.0;
    pub const USABLE_ACCURACY_M: f64 = 3000.0;
    pub const MAX_AGE_S: f64 = 30.0;

    /// Returns true when the search can stop now (a good fix).
    pub fn add_fix(&mut self, obj: &Value, accuracy: &Value, age_seconds: &Value) -> R<bool> {
        let (Some(accuracy), Some(age)) = (to_float(accuracy)?, to_float(age_seconds)?) else { return Ok(false) };
        if accuracy < 0.0 || age > Self::MAX_AGE_S {
            return Ok(false);
        }
        if self.best.is_none() || accuracy < self.best_accuracy.unwrap_or(f64::INFINITY) {
            self.best = Some(obj.clone());
            self.best_accuracy = Some(accuracy);
        }
        Ok(self.best_accuracy.unwrap_or(f64::INFINITY) <= Self::GOOD_ACCURACY_M)
    }

    /// Returns true when the error is final. `text` is `str(text)` already (the caller declines what it cannot render).
    pub fn add_error(&mut self, code: &Value, text: &str) -> R<bool> {
        let transient = matches!(to_int(code)?, Some(0) | Some(2));
        if transient {
            self.transient_error = text.to_string();
            return Ok(false);
        }
        self.fatal_error = text.to_string();
        Ok(true)
    }

    /// (fix, error text)
    pub fn result(&self) -> (Option<Value>, String) {
        if let (Some(best), Some(acc)) = (&self.best, self.best_accuracy) {
            if acc <= Self::USABLE_ACCURACY_M {
                return (Some(best.clone()), String::new());
            }
        }
        if !self.fatal_error.is_empty() {
            return (None, self.fatal_error.clone());
        }
        if let Some(acc) = self.best_accuracy {
            return (None, format!("Only a rough location was available (about {} m).", acc.trunc() as i64));
        }
        if !self.transient_error.is_empty() {
            return (None, self.transient_error.clone());
        }
        (None, "Timed out waiting for device location.".to_string())
    }
}
