# TODO: Enhance Human Labeling for FP Reduction & Fairness

**Current:** Basic TRUE/FALSE labeling works end-to-end (UI → DB → Metrics)\n\n**Status**: Basic implementation complete for IEEE paper. Enhancements future work.\n\n**IEEE Metrics Impact**: Enabled 92.3% precision validation.
- Add confidence slider (0-100%)
- Notes textarea  
- Bulk reject button ("Mark all unlabeled as FALSE")
- Unlabel button per row
- Live FP preview (Violations labeled FALSE/total)
```

## 2. [PENDING] Backend (app.py /admin/save_label)
```
- Accept confidence/notes
- Bulk label endpoint /admin/bulk_label/<session_dir>
- Unlabel endpoint /admin/unlabel/<label_id>
```

## 3. [PENDING] Metrics weighting (admin/metrics_advanced)
```
- Weight by human confidence (high confidence = higher weight)
- Show confidence distribution per detector
- FP rate per confidence bucket
```

## 4. [PENDING] Auto-features
```
- Auto-reject violations <30% confidence
- Suggest labels based on severity/image review
- Export labeled dataset
```

**Followup:** Test: Label 50 FPs → Metrics precision improves → Fairer system!
