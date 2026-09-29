package modelcatalog

import "testing"

func TestCatalogContainsApprovedBackbones(t *testing.T) {
	items := Approved()
	if len(items) != 4 {
		t.Fatalf("approved count = %d", len(items))
	}
	want := []string{DINOv3ViTSKey, ImageNetViTSKey, ResNet50Key, INatMobileNetKey}
	for index, item := range items {
		if item.Key != want[index] || item.SHA256 == "" || item.SizeBytes <= 0 || item.Revision == "" {
			t.Fatalf("catalog[%d] = %#v", index, item)
		}
	}
	if _, exists := Resolve("dinov3_vitb"); exists {
		t.Fatal("unapproved ViT-B must not resolve")
	}
}
