icehalo =
{
presets =
{
	AtmoHighClouds =
 	{
		visibleInGUI = true,
		readableName = _('Ice Halo on all mediums'),
		readableNameShort = 'AtmoHighClouds',
		thumbnailName = '', 
		atmosphereFactor = 1.000000,
		cirrusCloudsFactor = 2.000000,
		volumetricCloudsFactor = 3.000000,
	},
	CirrusOnly =
 	{
		visibleInGUI = true,
		readableName = _('Ice Halo on Cirrus'),
		readableNameShort = 'CirrusOnly',
		thumbnailName = '', 
		atmosphereFactor = 0.000000,
		cirrusCloudsFactor = 1.500000,
		volumetricCloudsFactor = 0.000000,
	},
	HighClouds =
 	{
		visibleInGUI = true,
		readableName = _('Ice Halo on Cirrus and high Volumetric Clouds'),
		readableNameShort = 'HighClouds',
		thumbnailName = '', 
		atmosphereFactor = 0.000000,
		cirrusCloudsFactor = 2.000000,
		volumetricCloudsFactor = 3.000000,
	},
	VolumetricOnly =
 	{
		visibleInGUI = true,
		readableName = _('Ice Halo on high Volumetric Clouds'),
		readableNameShort = 'VolumetricOnly',
		thumbnailName = '', 
		atmosphereFactor = 0.000000,
		cirrusCloudsFactor = 0.000000,
		volumetricCloudsFactor = 3.000000,
	},
},
crystalsPresets =
{
	AllKinds =
 	{
		visibleInGUI = true,
		readableName = '01 ##'.. _('All Kinds of Ice Halo'),
		readableNameShort = 'AllKinds',
		thumbnailName = 'Bazar/Effects/Thumbnails/Halo/AllKinds 1.png',
		appearance =
		{
			sunDiameterDeg = 0.530000,
			crystals =
			{
				random =
				{
					weight = 1.000000,
				},
				plate =
				{
					weight = 0.800000,
				},
				column =
				{
					weight = 0.700000,
				},
				parry =
				{
					weight = 0.300000,
				},
				lowitz =
				{
					weight = 0.200000,
				},
			}
		}
	},
	BasicHaloCircle =
 	{
		visibleInGUI = true,
		readableName = '02 ##'.. _('22 degree Ice Halo'),
		readableNameShort = 'BasicHaloCircle',
		thumbnailName = 'Bazar/Effects/Thumbnails/Halo/BasicHaloCircle 1.png',
		appearance =
		{
			sunDiameterDeg = 0.530000,
			crystals =
			{
				random =
				{
					weight = 1.000000,
				},
				plate =
				{
					weight = 0.000000,
				},
				column =
				{
					weight = 0.000000,
				},
				parry =
				{
					weight = 0.000000,
				},
				lowitz =
				{
					weight = 0.000000,
				},
			}
		}
	},
	BasicHaloWithSundogs =
 	{
		visibleInGUI = true,
		readableName = '03 ##'.. _('22 degree Ice Halo with Sundogs'),
		readableNameShort = 'BasicHaloWithSundogs',
		thumbnailName = 'Bazar/Effects/Thumbnails/Halo/BasicHaloWithSunDogs 1.png',
		appearance =
		{
			sunDiameterDeg = 0.530000,
			crystals =
			{
				random =
				{
					weight = 1.000000,
				},
				plate =
				{
					weight = 0.800000,
				},
				column =
				{
					weight = 0.000000,
				},
				parry =
				{
					weight = 0.000000,
				},
				lowitz =
				{
					weight = 0.000000,
				},
			}
		}
	},
	BasicSundogsTangents =
 	{
		visibleInGUI = true,
		readableName = '04 ##'.. _('22 degree Ice Halo with Sundogs and tangent arcs'),
		readableNameShort = 'BasicSundogsTangents',
		thumbnailName = 'Bazar/Effects/Thumbnails/Halo/BasicSundogsTangents 1.png',
		appearance =
		{
			sunDiameterDeg = 0.530000,
			crystals =
			{
				random =
				{
					weight = 1.000000,
				},
				plate =
				{
					weight = 0.800000,
				},
				column =
				{
					weight = 0.700000,
				},
				parry =
				{
					weight = 0.000000,
				},
				lowitz =
				{
					weight = 0.000000,
				},
			}
		}
	},
	SundogsArcs =
 	{
		visibleInGUI = true,
		readableName = '05 ##'.. _('Parhelia, arcs, subsun, subparhelia'),
		readableNameShort = 'SundogsArcs',
		thumbnailName = 'Bazar/Effects/Thumbnails/Halo/SundogsArcs 1.png',
		appearance =
		{
			sunDiameterDeg = 0.530000,
			crystals =
			{
				random =
				{
					weight = 0.100000,
				},
				plate =
				{
					weight = 1.000000,
				},
				column =
				{
					weight = 0.000000,
				},
				parry =
				{
					weight = 0.000000,
				},
				lowitz =
				{
					weight = 0.000000,
				},
			}
		}
	},
	Tangents =
 	{
		visibleInGUI = true,
		readableName = '06 ##'.. _('Circumscribed Ice Halo'),
		readableNameShort = 'Tangents',
		thumbnailName = 'Bazar/Effects/Thumbnails/Halo/Tangents 1.png',
		appearance =
		{
			sunDiameterDeg = 0.530000,
			crystals =
			{
				random =
				{
					weight = 0.200000,
				},
				plate =
				{
					weight = 0.000000,
				},
				column =
				{
					weight = 1.000000,
				},
				parry =
				{
					weight = 0.000000,
				},
				lowitz =
				{
					weight = 0.000000,
				},
			}
		}
	},
}
}
