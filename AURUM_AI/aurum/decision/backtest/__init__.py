"""Walk-forward replay of the deterministic pipeline. The AI reviewer is never called here: a model may know what happened
after a historical date, so its opinions on the past are not evidence."""
