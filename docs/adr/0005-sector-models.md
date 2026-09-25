# ADR-0005: Separate sector models, defined as versioned data

Status: Accepted

## Context
Revenue growth and FCF margin are meaningless for a bank; CET1 and NIM are meaningless for a chip maker.

## Decision
`config/sector_models.toml` defines 13 models (semiconductor, software, internet, consumer, financial,
biotech, healthcare, energy, industrials, REIT, utilities, technology hardware, generic) with metric
rules (bad → good), valuation rules, a primary multiple, a rationale and a minimum coverage. Selection is
explained to the user. Changes bump the version.

## Consequences
New sectors are configuration changes; missing KPIs reduce coverage rather than silently scoring zero.
