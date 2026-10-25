package migrations

import _ "embed"

//go:embed 001_initial.sql
var Initial string

//go:embed 002_watches.sql
var Watches string
