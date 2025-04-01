<?php
	$url = 'http://10.255.0.110/mgtv_hndx/EPGV2/GetChannelList?OutputType=json&Version=YYS.4.5.19.266.2.HNDX.0.0_Release_HW_4K&CategoryId=1000&MediaAssetsId=live';
	$jsonData = file_get_contents($url);
	$data = json_decode($jsonData, true);
	$xml = new SimpleXMLElement('<?xml version="1.0" encoding="UTF-8"?><tv></tv>');
	$xml->addAttribute("generator-info-name", "湖南电信IPTV-EPG");
	$iptvlist = $data["l"]["il"];
	foreach ($iptvlist as $item) {
		$channl=$xml->addChild("channel");
		//$channl->addAttribute("id", $item["arg_list"]["channel_id"]);
		$channl->addAttribute("id", $item["name"]);
		$displayname = $channl->addChild("display-name", $item["name"]);
		$displayname->addAttribute("lang", "zh");
		$url2 = "http://10.255.0.110/mgtv_hndx/BasicIndex/GetPlaybill?AfterDay=1&TimeZone=8&OutputType=json&Version=YYS.4.5.19.266.2.HNDX.0.0_Release_HW_4K&VideoType=1&Mode=relative&VideoId=".$item["id"]."&BeforeDay=0";
		//$url2 = "http://10.255.9.200/IPTV_EPG/Channel/GetPlaybillsByChannelId?channelId=".$item["arg_list"]["channel_id"];
		$menudata = file_get_contents($url2);
		$menulist = json_decode($menudata, true);
		
		//$filePath = "/tmp/localfile.json";  // 文件名及路径
		//file_put_contents($filePath, $menudata);
		
		foreach ($menulist["day"] as $item1) {
			foreach ($item1["item"] as $v) {
				$hour = intval(substr($v['begin'], 0, 2));
				$minute = intval(substr($v['begin'], 2, 2));
				$second = intval(substr($v['begin'], 4, 2));

				$startSeconds = $hour * 3600 + $minute * 60 + $second;
				$endSeconds = $startSeconds + intval($v['time_len']);

				$endHour = str_pad(intval($endSeconds / 3600), 2, "0", STR_PAD_LEFT);
				$endMinute = str_pad(intval(($endSeconds % 3600) / 60), 2, "0", STR_PAD_LEFT);
				$endSecond = str_pad($endSeconds % 60, 2, "0", STR_PAD_LEFT);
				
				$startTime = $item1["day"].$v['begin'];
				$endTime = $item1["day"].$endHour.$endMinute.$endSecond;
				
				//$v["start"]=$item1["day"].$v["begin"];
				//$v["stop"]=$v["start"]+$v["time_len"];
				
				$programme=$xml->addChild("programme");
				$programme->addAttribute("channel", $item["name"]);
				$programme->addAttribute("start", $startTime." +0800");
				$programme->addAttribute("stop", $endTime." +0800");
				$tvlist = $v["text"];
				$newtvlist = convertPunctuationToFullWidth($tvlist);
				$title = $programme->addChild("title", $newtvlist);
				$title->addAttribute("lang", "zh");
				$desc = $programme->addChild("desc");
				$desc->addAttribute("lang", "zh");
			}
		}
		
		/*foreach ($menulist["datas"] as $item1) {
			foreach ($item1["scheduleList"] as $v) {
					$programme = $xml->addChild("programme");
					$programme->addAttribute("channel", $v["channelId"]);
					$programme->addAttribute("start", $v["day"].$v["beginTime"]." +0800");
					$programme->addAttribute("stop", $v["day"].$v["endTime"]." +0800");
					$tvlist = $v["name"];
					$newtvlist = convertPunctuationToFullWidth($tvlist);
					$title = $programme->addChild("title", $newtvlist);
					$title->addAttribute("lang", "zh");
					$desc = $programme->addChild("desc");
					$desc->addAttribute("lang", "zh");
			}
		}*/
	}
	header("Content-type: application/xml; charset=UTF-8");
	echo $xml->asXML();
	
	function convertPunctuationToFullWidth($input) {
		// 半角标点符号与全角标点符号的映射
		$punctuationMap = [
			'.' => '。',
			',' => '，',
			'?' => '？',
			'!' => '！',
			':' => '：',
			';' => '；',
			'"' => '“',
			'\'' => '‘',
			'(' => '（',
			')' => '）',
			'[' => '【',
			']' => '】',
			'{' => '｛',
			'}' => '｝',
			'<' => '《',
			'>' => '》',
			//'-' => '－',
			'_' => '—',
			'@' => '＠',
			'#' => '＃',
			'$' => '＄',
			'%' => '％',
			'&' => '＆',
			'*' => '＊',
			'+' => '＋',
			'=' => '＝',
			'/' => '／',
			'\\' => '＼',
			'^' => '＾',
			'`' => '｀',
			'~' => '～'
		];

		// 用正则表达式替换标点符号
		return strtr($input, $punctuationMap);
	}
?>
