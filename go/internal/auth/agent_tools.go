package auth

// AgentToolAllowed is the shared action policy for the tool bridge and catalog.
// Individual resource handlers still enforce their existing visibility rules.
func AgentToolAllowed(role Role, name string) bool {
	if role == Admin || role == Business {
		return true
	}
	return role == Annotator && (name == "datasets.list" || name == "datasets.get_version" || name == "models.list_versions" || name == "models.get_version")
}
