package postgres

import (
	"context"
	"encoding/json"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
)

func serializedTraceContext(ctx context.Context) []byte {
	carrier := propagation.MapCarrier{}
	otel.GetTextMapPropagator().Inject(ctx, carrier)
	payload, err := json.Marshal(map[string]string(carrier))
	if err != nil {
		return []byte(`{}`)
	}
	return payload
}
