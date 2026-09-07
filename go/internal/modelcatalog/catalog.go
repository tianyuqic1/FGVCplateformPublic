package modelcatalog

import "strings"

// Backbone is an immutable training choice exposed by the control plane.
// Key is the public contract; ModelName is the exact timm architecture name.
type Backbone struct {
	Key                string
	LegacyExtractor    string
	DisplayName        string
	Architecture       string
	ModelName          string
	ModelID            string
	Revision           string
	PretrainingMethod  string
	PretrainingDataset string
	InputSize          int
	FeatureDim         int
	ParameterCount     int64
	Pooling            string
	SHA256             string
	SizeBytes          int64
	License            string
	LicenseURL         string
	LFSPath            string
}

const (
	DINOv3ViTSKey   = "dinov3_vits16_lvd1689m"
	ImageNetViTSKey = "imagenet_vits16_augreg_in21k_ft_in1k"
	ResNet50Key     = "imagenet_resnet50_a1_in1k"
)

var approved = []Backbone{
	{
		Key: DINOv3ViTSKey, LegacyExtractor: "dinov3_vits", DisplayName: "ViT-S/16 · DINOv3 (LVD-1689M)",
		Architecture: "vit_small_patch16", ModelName: "vit_small_patch16_dinov3", ModelID: "timm/vit_small_patch16_dinov3.lvd1689m",
		Revision: "3bf4720a82ec2066db88137180ff1f83a675cef0", PretrainingMethod: "DINOv3", PretrainingDataset: "LVD-1689M",
		InputSize: 224, FeatureDim: 384, ParameterCount: 21_588_480, Pooling: "cls",
		SHA256: "2a1ec16ae28ffa07bc0ead0241ee7df9fc26451fe6f9f839b7b3afa0a906b040", SizeBytes: 86_362_376,
		License: "DINOv3 License", LicenseURL: "https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md",
		LFSPath: "weights/pretrained/dinov3/vit_small_patch16_dinov3.lvd1689m.safetensors",
	},
	{
		Key: ImageNetViTSKey, LegacyExtractor: "imagenet_vits", DisplayName: "ViT-S/16 · ImageNet-21K → ImageNet-1K",
		Architecture: "vit_small_patch16", ModelName: "vit_small_patch16_224.augreg_in21k_ft_in1k", ModelID: "timm/vit_small_patch16_224.augreg_in21k_ft_in1k",
		Revision: "7e2c55630205e1266030f18370f4c6ed1a514b52", PretrainingMethod: "supervised", PretrainingDataset: "ImageNet-21K → ImageNet-1K",
		InputSize: 224, FeatureDim: 384, ParameterCount: 22_050_664, Pooling: "model",
		SHA256: "79c03c635cdfd798a364a9d8c4e5c0b7255b975ea2c9616046d4f77ab01435aa", SizeBytes: 88_216_496,
		License: "Apache-2.0", LicenseURL: "https://github.com/huggingface/pytorch-image-models/blob/main/LICENSE",
		LFSPath: "weights/pretrained/imagenet/vit_small_patch16_224.augreg_in21k_ft_in1k.safetensors",
	},
	{
		Key: ResNet50Key, LegacyExtractor: "imagenet_resnet50", DisplayName: "ResNet-50 · ImageNet-1K",
		Architecture: "resnet50", ModelName: "resnet50.a1_in1k", ModelID: "timm/resnet50.a1_in1k",
		Revision: "767268603ca0cb0bfe326fa87277f19c419566ef", PretrainingMethod: "supervised", PretrainingDataset: "ImageNet-1K",
		InputSize: 288, FeatureDim: 2048, ParameterCount: 25_557_032, Pooling: "model",
		SHA256: "773525d5821de224f8f30c33377b7a795d7863e08522698200d3217d3f2a41bb", SizeBytes: 102_469_840,
		License: "Apache-2.0", LicenseURL: "https://github.com/huggingface/pytorch-image-models/blob/main/LICENSE",
		LFSPath: "weights/pretrained/imagenet/resnet50.a1_in1k.safetensors",
	},
}

func Approved() []Backbone {
	result := make([]Backbone, len(approved))
	copy(result, approved)
	return result
}

func Resolve(key string) (Backbone, bool) {
	key = strings.TrimSpace(key)
	for _, candidate := range approved {
		if key == candidate.Key || key == candidate.LegacyExtractor {
			return candidate, true
		}
	}
	return Backbone{}, false
}
