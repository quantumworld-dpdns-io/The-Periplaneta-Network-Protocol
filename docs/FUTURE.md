# Future work (v2)

Items from the original proposal deliberately left out of the prototype.

1. **Transcriptomic bridge (wet-lab).** RNA-seq of insect ganglia during neural
   repair, cross-matched against human neurodegeneration gene sets. Requires
   wet-lab data; **not done**. The war-room now shows a *literature* ion-channel
   ortholog table (item 2) so the translational argument is visible without
   pretending tissue was sequenced. The live Transformer / GRU still do not
   ingest RNA-seq.
2. **Conserved ion-channel panel.** Shipped as the dashboard "Conserved
   channels" table (`dashboard/lib/orthologs.ts`): para/Nav, Shaker/Kv, Rdl
   GABA-A, Dop1R, nAChR, DAT, GluCl, eag mapped to HGNC symbols. Static
   FlyBase/HGNC mapping, not expression.
3. **Real recordings on the HIL nodes, validated on a board.** `ml/scripts/make_template.jl`
   now emits the firmware's `TPL_*` header directly from the Zenodo recordings;
   what remains is running it on a machine with Julia and the data, flashing an
   ESP32-S3, and checking the rendered waveform against the emulator.
4. **Lag-based autoscaling.** KEDA scaler on `feature-worker` consumer lag
   instead of CPU HPA.
5. **Observability.** kube-prometheus-stack with a Grafana board for
   frames/s, consumer lag, alerts/s.
6. **Live-animal extension (if ever pursued).** Would require IACUC-equivalent
   review even for invertebrates in many institutions; the HIL design is the
   intended demo path.
